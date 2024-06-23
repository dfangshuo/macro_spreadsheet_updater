import asyncio
import json
import os
import tempfile

import modal

from sheets_get_values import sheets_get_values, construct_google_application_credentials

image = modal.Image.debian_slim(python_version="3.8").pip_install_from_requirements(
  "requirements.txt"
)

app = modal.App()

@app.function(
  image=image,
  secrets=[modal.Secret.from_name("spreadsheet-telegram-intg-v0")],
  schedule=modal.Cron("0 17 * * *")
)
def main():
  token = os.environ['TELEGRAM_TOKEN']
  SHEETS_SPREADSHEET_ID = os.environ['SHEETS_SPREADSHEET_ID']
  write_chat_id = os.environ['TELEGRAM_WRITE_CHAT_ID']
  with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as temp_file:
    creds = construct_google_application_credentials(
      os.environ['PRIVATE_KEY_ID'],
      os.environ['PRIVATE_KEY'].encode().decode('unicode_escape'),
      os.environ['CLIENT_ID'],
    )
    json.dump(creds, temp_file)
    path = temp_file.name
    path_to_cleanup = path
  
  dry_run = bool(int(os.environ['DRY_RUN_INT']))
  asyncio.run(sheets_get_values(token, SHEETS_SPREADSHEET_ID, path, write_chat_id, dry_run=dry_run))
  print(f"Cleaning up {path}")
  os.remove(path_to_cleanup)
    
