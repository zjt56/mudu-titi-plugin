# Windows transactional repair file mode fallback

## 背景（Background）

Running the existing local repair transaction on Windows would fail because `os.fchmod` is not available there. The reported live source-material issue has since recovered through other activity; this change does not add or execute a batch material-recovery action.

## 改了什么（Change）

`app/workflow/self_heal_actions.py::_atomic_raw` uses `os.fchmod` where supported and applies the same captured mode to the temporary path with `os.chmod` otherwise.

## 边界（Boundary）

No live batch data, manifest, upload index, or remote state was changed. No source-material recovery API was added. The fallback only keeps existing transactional writes portable.

## 验证（Verification）

The focused fallback check passed: one isolated atomic write with `os.fchmod` hidden, exercising the `os.chmod` branch. `python -m unittest workflow.test_self_heal_actions` ran 23 tests and ended with 5 errors: existing test assertions expect `06_batch/batch.json`, while this checkout's repair code uses its current batch-fill path layout, producing `KeyError: '06_batch/batch.json'`. A preceding default-code-page run had 8 errors, including fixture text decoding failures; rerun with Python UTF-8 mode removed the decoding failures but the 5 path-expectation errors remained. No pass count can be claimed for that suite. 该套件未通过（5 项错误为既有路径断言，与本次改动无关）；本条改动自身仅通过上述 1 项定向验证。

## 回退（Rollback）

Revert the conditional mode-setting line in `_atomic_raw`; no data migration or recovery receipt is involved.
