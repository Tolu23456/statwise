#!/bin/bash

cd /vercel/share/v0-project/statwise

echo "[v0] Installing dependencies..."
npm install

echo "[v0] Starting Expo dev server..."
npm run dev
