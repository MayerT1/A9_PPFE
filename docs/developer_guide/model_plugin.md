# Hosting a model on A9_PPFE

The platform owns everything except the model's forward pass:

- HRRR fetch and regrid
- normalization (from the model card)
- tiling with halos, batching and device selection
- local or remote serving
- decision layers, UI and reports

## Contract (`src/ppfe/models/base.py`)

```python
class MyModel(FoundationModel):
    card = ModelCard(name=..., version=..., scale_factor=4, receptive_halo=32,
                     inputs=[VarSpec("apcp", "mm", mean, std), ...],
                     outputs=[VarSpec("apcp_ds", "mm", mean, std), ...])
    def load(self, device: str) -> None: ...                         # once, kept warm
    def predict_normalized(self, x: np.ndarray) -> np.ndarray: ...   # [B,Cin,h,w] -> [B,Cout,h*s,w*s]
```

Select the model with `PPFE_MODEL=package.module:ClassName`. A filled-in skeleton is in `plugins/prithvi_wxc_template.py`.

## Rules the platform relies on

| Rule | Why |
|---|---|
| Input names must exist in `ppfe.data.hrrr.HERBIE_SEARCH`. Add a regex and unit conversion there for new HRRR variables. | That table is how fetch/stage finds each variable. |
| `receptive_halo` must be at least the model's spatial context, in input pixels. | Tiles are cut with that halo and cropped, so tiled output equals untiled output. `tests/unit/test_core.py::test_engine_shapes_and_tiling_is_seamless` checks this. |
| An output named `apcp_ds` drives flow routing and the hazard index. | Other `*_ds` outputs get map layers automatically. |
| Return float32. | Denormalization uses the card's output stats. |

## Making it fast

- **Run ONNX Runtime** (`make_session`), which picks CUDA → DirectML → CoreML → CPU. Pin a provider with `PPFE_DEVICE=cuda|trt|dml|cpu`.
- **Do heavy compute at input resolution, upsample late.** Use pixel shuffle or a transposed conv. The placeholder does this: about 0.35 s for 512² → 2048² on 2 CPU cores.
- **Avoid bicubic `Resize` in ONNX on CPU.** It measured about 100× slower than bilinear.
- **Tune tiling.** Raise `PPFE_TILE_SIZE` and `PPFE_TILE_BATCH` until GPU memory is full.
- **Ensembles: batch the members.** Stack members on the batch axis in one call and reduce afterwards. Expose spread as extra outputs (for example `apcp_ds_p90`).
- **Benchmark** with `ppfe bench --size 1024`.

## Remote / cloud inference

Run `ppfe worker` (or `docker compose --profile gpu up`) on the GPU machine. On the portal, set:

```
PPFE_INFERENCE_BACKEND=remote
PPFE_REMOTE_URL=https://gpu-host:8100
PPFE_REMOTE_TOKEN=...
```

The wire format is `np.save` bytes, raw units. The worker runs the same tiling engine.
