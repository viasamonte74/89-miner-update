module.exports = {
  apps: [{
    name: 'sn89-rabi-h1-submit',
    cwd: '/workspace/InfiniteQuant-Subnet',
    script: 'neurons/miner.py',
    interpreter: '/venv/sn89/bin/python',
    autorestart: false,
    env: {
      PYTHONPATH: '/workspace/InfiniteQuant-Subnet',
      SN89_DATA_ROOT: '/workspace/sn89_data',
    },
    // SN89_FEED_TOKEN loaded below via ecosystem merge — use bash wrapper instead
  }],
};
