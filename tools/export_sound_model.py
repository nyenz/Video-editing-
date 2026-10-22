"""Build the sound-splitting model file (sound-split-1.onnx) that Nyenz Edit downloads once.

The model is BandIt Plus (speech / music / effects) by Roman Solovyev (ZFTurbo), from
https://github.com/ZFTurbo/Music-Source-Separation-Training  (MIT licence for the code). It is a PyTorch
model; the app runs it with the small onnxruntime library instead, so it is converted here:

* only real numbers are used (ONNX has no complex numbers): the network returns the three masks, and the
  app multiplies them with the spectrum itself (see editforge/splitter.py);
* the weights are stored as 16-bit numbers (half the download) and turned back into 32-bit when loaded,
  so speed and results are the same as with the full file (checked below).

Run (done by .github/workflows/sound-model.yml):

    python tools/export_sound_model.py --repo path/to/Music-Source-Separation-Training \
        --weights model_bandit_plus_dnr_sdr_11.47.chpt --config config_dnr_bandit_bsrnn_multi_mus64.yaml --out sound-split-1.onnx
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import tempfile
import types
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, help="a checkout of ZFTurbo/Music-Source-Separation-Training")
    ap.add_argument("--weights", required=True)
    ap.add_argument("--config", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    import numpy as np
    import onnx
    import onnxruntime as ort
    import torch
    import torch.nn.functional as Fn
    import yaml
    from onnx import TensorProto, helper, numpy_helper
    from torch import nn

    repo = str(Path(args.repo).resolve())
    sys.path.insert(0, repo)
    pkg = types.ModuleType("models.bandit.core")           # skip the training-only code in the package's __init__
    pkg.__path__ = [repo + "/models/bandit/core"]
    sys.modules["models.bandit.core"] = pkg
    from models.bandit.core.model import MultiMaskMultiSourceBandSplitRNNSimple

    cfg = yaml.safe_load(open(args.config))
    model = MultiMaskMultiSourceBandSplitRNNSimple(**cfg["model"])
    sd = torch.load(args.weights, map_location="cpu", weights_only=False)
    if isinstance(sd, dict) and "state_dict" in sd:
        sd = sd["state_dict"]
    print(model.load_state_dict(sd))
    model.eval()

    class Core(nn.Module):
        """The network with real numbers only: (N, 1025, T, 2) spectrogram in, (3, N, 1025, T, 2) masks out."""

        def __init__(self, m):
            super().__init__()
            self.core = m.bsrnn

        def forward(self, x):
            core = self.core
            n, freq, frames, _ = x.shape
            xr = x.permute(0, 2, 3, 1).unsqueeze(2)                       # (N, T, 1, 2, F)
            zs = []
            for i, nfm in enumerate(core.band_split.norm_fc_modules):
                fs, fe = core.band_split.band_specs[i]
                zs.append(nfm(xr[..., fs:fe].reshape(n, frames, 1, -1)))
            q = core.tf_model(torch.stack(zs, dim=1))                     # (N, bands, T, emb)
            outs = []
            for _stem, mem in core.mask_estim.items():
                total = None
                for b, nmlp in enumerate(mem.norm_mlp):
                    l1, l2 = getattr(nmlp.hidden, "0"), getattr(nmlp.output, "0")
                    h = torch.tanh(Fn.linear(nmlp.norm(q[:, b, :, :]), l1.weight, l1.bias))
                    mb = Fn.glu(Fn.linear(h, l2.weight, l2.bias), dim=-1)
                    mb = mb.reshape(n, frames, 1, nmlp.bandwidth, 2).permute(0, 2, 3, 1, 4)
                    fs, fe = mem.band_specs[b]
                    if mem.use_freq_weights:
                        mb = mb * mem.get_buffer(f"freq_weights/{b}")[:, None][None, None, :, :, None]
                    mb = Fn.pad(mb, (0, 0, 0, 0, fs, freq - fe))
                    total = mb if total is None else total + mb
                outs.append(total[:, 0])
            return torch.stack(outs, dim=0)

    core = Core(model).eval()
    frames = cfg["audio"]["chunk_size"] // cfg["model"]["hop_length"] + 1
    torch.manual_seed(0)
    probe = torch.randn(1, 1025, frames, 2) * 0.05
    with torch.no_grad():
        masks = core(probe)
        original = model.bsrnn(torch.view_as_complex(probe.contiguous()).unsqueeze(1), cond=None)["spectrogram"]
        for k, stem in enumerate(cfg["model"]["stems"]):          # the rewrite must equal the original network
            m = masks[k]
            a, b = probe[..., 0], probe[..., 1]
            mine = torch.stack([a * m[..., 0] - b * m[..., 1], a * m[..., 1] + b * m[..., 0]], dim=-1)
            diff = float((torch.view_as_real(original[stem][:, 0]) - mine).abs().max())
            print(stem, "difference to the original network:", diff)
            assert diff < 1e-5, "the rewritten network does not match the original"
        small = torch.randn(2, 1025, 40, 2) * 0.05
        tmp = Path(tempfile.mkdtemp()) / "full.onnx"
        torch.onnx.export(core, (small,), str(tmp), input_names=["spec"], output_names=["masks"], opset_version=17, dynamo=False,
                          dynamic_axes={"spec": {0: "n", 2: "t"}, "masks": {1: "n", 3: "t"}})

    m = onnx.load(str(tmp))
    g = m.graph
    inits, casts, half = [], [], 0
    for init in g.initializer:                                     # big weights are stored as 16-bit and widened on load
        if init.data_type == TensorProto.FLOAT and int(np.prod(init.dims)) >= 1024:
            h = numpy_helper.from_array(numpy_helper.to_array(init).astype(np.float16), name=init.name + "__h")
            inits.append(h)
            casts.append(helper.make_node("Cast", [h.name], [init.name], to=TensorProto.FLOAT, name="cast_" + init.name))
            half += 1
        else:
            inits.append(init)
    nodes = list(g.node)
    del g.initializer[:]
    g.initializer.extend(inits)
    del g.node[:]
    g.node.extend(casts + nodes)
    onnx.save(m, args.out)

    for n in (1, 2):                                               # the file must agree with the PyTorch network
        x = (torch.randn(n, 1025, frames, 2) * 0.05)
        with torch.no_grad():
            want = core(x).numpy()
        sess = ort.InferenceSession(args.out, providers=["CPUExecutionProvider"])
        got = sess.run(None, {"spec": x.numpy()})[0]
        err = float(np.abs(got - want).max())
        print(f"channels={n}: largest difference to PyTorch {err:.5f} (masks reach {float(np.abs(want).max()):.2f})")
        assert err < 0.02, "the ONNX file does not match the PyTorch network"
    data = Path(args.out).read_bytes()
    print(f"{args.out}: {len(data):,} bytes, {half} weight tensors stored as 16-bit, sha256 {hashlib.sha256(data).hexdigest()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
