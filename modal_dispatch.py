import asyncio
import json
import os
import tempfile

import modal
from fastapi import Request

from sheets_get_values import sheets_get_values, construct_google_application_credentials

image = modal.Image.debian_slim(python_version="3.12").pip_install_from_requirements(
  "requirements.txt"
).add_local_file("sheets_get_values.py", remote_path="/root/sheets_get_values.py")

app = modal.App("ts_fsc1_v1")

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


_LETTER_TO_CATEGORY = {
  "W": "WEIGHT",
  "C": "CALORIES",
  "S": "STEPS",
  "P": "PROTEIN",
}

@app.function(
  image=image,
  secrets=[modal.Secret.from_name("spreadsheet-telegram-intg-v0")],
)
@modal.fastapi_endpoint(method="GET")
def log_and_post(request: Request, text: str = ""):
  print(f"Full URL: {request.url}")
  print(f"Query params: {dict(request.query_params)}")
  print(f"Text param: {text}")

  input_data = {}
  for line in text.strip().splitlines():
    line = line.strip()
    if not line:
      continue
    letter = line[0].upper()
    if letter not in _LETTER_TO_CATEGORY:
      print(f"Skipping unrecognized line: {line!r}")
      continue
    value = line[1:].strip()
    input_data[_LETTER_TO_CATEGORY[letter]] = value

  print(f"Parsed input_data: {input_data}")

  SHEETS_SPREADSHEET_ID = os.environ['SHEETS_SPREADSHEET_ID']
  with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as temp_file:
    creds = construct_google_application_credentials(
      os.environ['PRIVATE_KEY_ID'],
      os.environ['PRIVATE_KEY'].encode().decode('unicode_escape'),
      os.environ['CLIENT_ID'],
    )
    json.dump(creds, temp_file)
    creds_path = temp_file.name

  dry_run = bool(int(os.environ.get('DRY_RUN_INT', '1')))
  try:
    message = asyncio.run(sheets_get_values(
      telegram_token=os.environ.get('TELEGRAM_TOKEN', ''),
      sheets_spreadsheet_id=SHEETS_SPREADSHEET_ID,
      google_application_credentials_path=creds_path,
      telegram_write_chat_id=os.environ.get('TELEGRAM_WRITE_CHAT_ID', ''),
      dry_run=dry_run,
      input_data=input_data,
    ))
  finally:
    print(f"Cleaning up {creds_path}")
    os.remove(creds_path)

  return {"status": "ok", "received_text": text, "message": message}

