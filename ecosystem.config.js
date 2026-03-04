const path = require('path');
const cwd = __dirname;

// Find venv python — check local .venv first, then parent duo/.venv
const fs = require('fs');
let pythonBin = 'python3';
const localVenv = path.join(cwd, '.venv', 'bin', 'python');
const parentVenv = path.join(cwd, '..', '..', '.venv', 'bin', 'python');

// On macOS dev: duo/.venv already has permissions. On Linux server: local .venv
if (fs.existsSync(parentVenv)) {
  pythonBin = parentVenv;
} else if (fs.existsSync(localVenv)) {
  pythonBin = localVenv;
}

module.exports = {
  apps: [
    {
      name: "hourly-paper",
      script: pythonBin,
      args: "-m hourly_live start --now --port 8080 --max-positions 5",
      cwd: cwd,
      interpreter: "none",
      autorestart: true,
      max_restarts: 10,
      restart_delay: 60000,
      watch: false,
      log_date_format: "YYYY-MM-DD HH:mm:ss",
      error_file: path.join(cwd, "logs", "error.log"),
      out_file: path.join(cwd, "logs", "output.log"),
      merge_logs: true,
      env: {
        HOURLY_DB: "sqlite",
        // Parent dir so `python -m hourly_live` can find the package
        PYTHONPATH: path.resolve(cwd, '..'),
      },
    },
  ],
};
