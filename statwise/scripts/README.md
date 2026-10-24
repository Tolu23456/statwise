# StatWise Web Start Scripts

Expo CLI tries to launch a browser and fails in this environment (missing `/usr/bin/garcon-url-handler`).
To avoid that crash, always use these scripts.

## Start the app
```bash
cd statwise
npm run web:bg
# or
bash scripts/start-web.sh
```

## Stop the app
```bash
npm run web:stop
# or
bash scripts/stop-web.sh
```

The app will be available at http://localhost:8081. Logs go to `/tmp/statwise-web.log`.
