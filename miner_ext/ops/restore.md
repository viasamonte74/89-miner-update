# Restore procedure (no volume — assume container wipe)

1. Recreate dirs: `bash miner_ext/ops/bootstrap.sh`
2. Restore `sn89_data/` from the latest off-box backup into `/workspace/sn89_data/`
3. Restore `miner_ext/models/` and `miner_ext/results/`
4. Re-run archiver (resume/skip existing):  
   `python -m miner_ext.data.tick_archiver --workers 2`
5. `python -m miner_ext.ops.healthcheck`
6. `pytest miner_ext/tests -q`
7. Hotkey only: copy hotkey files with mode 600 from your secure machine — never coldkey/mnemonic

Do not restore `.env` secrets from untrusted backups; re-inject env vars manually.
