#!/bin/bash
# Double-click this file. It rebuilds RightHour Lite from your latest files and opens the new version.
cd "$(dirname "$0")" || exit 1
PORT=8501

PY=$(command -v python3 || command -v python)
if [ -z "$PY" ]; then
  echo "Python 3 is not installed. Install it from https://www.python.org/downloads/ and double-click this file again."
  read -r -p "Press Enter to close."; exit 1
fi

# 1. Stop any old copy still running, so you never see the old version.
OLD=$(lsof -ti tcp:$PORT 2>/dev/null)
if [ -n "$OLD" ]; then
  echo "Stopping the old copy..."
  kill $OLD 2>/dev/null; sleep 1
  kill -9 $OLD 2>/dev/null
fi

# 2. Make the environment if it does not exist.
if [ ! -x .venv/bin/python ]; then
  echo "First run: setting things up (about 2 minutes, once)..."
  "$PY" -m venv .venv || { echo "Could not create the environment."; read -r -p "Press Enter to close."; exit 1; }
  .venv/bin/python -m pip install --quiet --upgrade pip
fi

# 3. Install packages again only if requirements.txt changed.
SUM=$(shasum requirements.txt | cut -d' ' -f1)
if [ "$SUM" != "$(cat .venv/.req_hash 2>/dev/null)" ]; then
  echo "Updating packages..."
  .venv/bin/python -m pip install --quiet -r requirements.txt || { echo "Install failed. Check your internet and try again."; read -r -p "Press Enter to close."; exit 1; }
  echo "$SUM" > .venv/.req_hash
fi

# 4. Delete old compiled and cached files, so only your newest code runs.
find . -path ./.venv -prune -o -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null
rm -rf ~/.streamlit/cache 2>/dev/null

# 5. Publish: send your latest changes to GitHub. The live app updates by itself in about a minute.
#    It skips this step if the code has an error, so a broken version never goes live.
if [ -d .git ] && [ -n "$(git status --porcelain)" ]; then
  if .venv/bin/python -m py_compile app.py righthour/*.py 2>/dev/null; then
    echo "Publishing your changes to the live app..."
    git add -A && git commit -q -m "Update $(date '+%Y-%m-%d %H:%M')" \
      && git push -q 2>/dev/null && echo "Published. Live app updates in about a minute." \
      || echo "Could not publish (offline?). Your local app still works."
  else
    echo "Code has an error, so I did NOT publish. Fix it and double-click again."
  fi
fi

# 6. Start fresh and open the browser.
echo "Starting RightHour Lite at http://localhost:$PORT . Close this window to stop it."
( sleep 4; open "http://localhost:$PORT" ) &
exec .venv/bin/python -m streamlit run app.py --server.headless true --server.port $PORT --browser.gatherUsageStats false
