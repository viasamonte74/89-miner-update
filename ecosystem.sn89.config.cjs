// Prefer scripts/pm2_lf_auto.sh / pm2_hf_auto.sh (fleet). Bare miner.py needs a
// subcommand (serve / submit / …) and exits immediately if invoked with none.
module.exports = {
  apps: [{
    name: 'sn89-rabi-h1-submit',
    cwd: '/workspace/InfiniteQuant-Subnet',
    script: 'scripts/pm2_lf_auto.sh',
    interpreter: 'bash',
    autorestart: true,
    env: {
      PYTHONPATH: '/workspace/InfiniteQuant-Subnet',
      SN89_DATA_ROOT: '/workspace/sn89_data',
      SN89_WALLET_NAME: 'rabi',
      SN89_WALLET_HOTKEY: 'rabi-h1',
    },
  }],
};
