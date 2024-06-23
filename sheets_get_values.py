import asyncio
import datetime as dt
import os
from pytz import timezone 

import json
import tempfile

import re
import requests
from typing import Tuple

import google.auth
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

import telegram
from telegram.ext import Application, Updater, CommandHandler

_EST = timezone('US/Eastern')
_SHEET_NAME = "C1 Weight, Nutrition, Steps"
_EPOCH_ZERO = _EST.localize(dt.datetime(2023, 12, 4))
_SHEETS_COL_ROW_PATTERN = r'([A-Za-z]+)(\d+)'

NUM_DAYS_IN_WEEK = 7
_WEIGHT = "WEIGHT"
_STEPS = "STEPS"
_CALORIES = "CALORIES"
_PROTEIN = "PROTEIN"

_YESTERDAY_CATEGORIES = {
  _STEPS: "B25",
  _CALORIES: "B34",
  _PROTEIN: "B43",
}

_TODAY_CATEGORIES = {
  _WEIGHT: "B6",
}

_CATEGORIES_ORDER = [
  _WEIGHT, _CALORIES, _PROTEIN, _STEPS
]
_EXPECTED_INPUT_ARRAY_LEN = len(_CATEGORIES_ORDER)


def extract_col_row(cell):
  # Extracts the column letter(s) and row number from the cell reference
  col = ''.join(filter(str.isalpha, cell))
  row = int(''.join(filter(str.isdigit, cell)))
  return col, row


def increment_column(col, offset):
  # Increment the column letter by the given offset, handling transitions like 'Z' to 'AA'
  result = []
  col_value = sum((ord(char) - ord('A') + 1) * (26 ** i) for i, char in enumerate(reversed(col)))
  col_value += offset
  while col_value > 0:
    col_value, remainder = divmod(col_value - 1, 26)
    result.append(chr(remainder + ord('A')))
  return ''.join(reversed(result))


def get_cell(today: dt.datetime, epoch_zero_cell: str, epoch_zero: dt.datetime) -> str:
  days_passed = (today - epoch_zero).days

  # Determine the column letter based on the epoch_zero_cell
  col, row = extract_col_row(epoch_zero_cell)
  col_offset = days_passed // NUM_DAYS_IN_WEEK  # Number of weeks passed
  new_col = increment_column(col, col_offset)

  # Determine the new row number
  row_offset = days_passed % NUM_DAYS_IN_WEEK
  new_row = row + row_offset

  return f"{new_col}{new_row}"


def get_values(spreadsheet_id, range_name) -> str:
  """
  Creates the batch_update the user has access to.
  Load pre-authorized user credentials from the environment.
  """
  creds, _ = google.auth.default()
  # pylint: disable=maybe-no-member
  try:
    service = build("sheets", "v4", credentials=creds)

    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=range_name)
        .execute()
    )
  except HttpError as error:
    print(f"An error occurred: {error}")
    return error

  rows = result.get("values", [])

  # Expect a matrix of 1 item
  try:
    return str(rows[0][0])
  except IndexError:
    # This can happen when there's no data
    return ""


def update_values(spreadsheet_id, range_name, value_input_option, values):
  """
  Creates the batch_update the user has access to.
  Load pre-authorized user credentials from the environment.
  """
  creds, _ = google.auth.default()
  # pylint: disable=maybe-no-member
  try:
    service = build("sheets", "v4", credentials=creds)
    body = {"values": values}
    result = (
        service.spreadsheets()
        .values()
        .update(
            spreadsheetId=spreadsheet_id,
            range=range_name,
            valueInputOption=value_input_option,
            body=body,
        )
        .execute()
    )
    print(f"{result.get('updatedCells')} cells updated.")
    return result
  except HttpError as error:
    print(f"An error occurred: {error}")
    return error


async def send_message(bot: telegram.Bot, msg: str, chat_id: str):
  await bot.send_message(chat_id=chat_id, text=msg)


async def get_message_by_offset(bot: telegram.Bot, offset: int):
  """
  TODO: figure out what offset actually does
  """
  updates = await bot.get_updates(offset=offset)
  unprocessed_messages = [update.message for update in updates if update.message is not None]
  if not unprocessed_messages:
    raise ValueError(f"Did not get any messages. Did you enter an input today?")

  return unprocessed_messages[0]


def get_or_update_cell(
  d: dt.datetime, description: str, epoch_zero_cell: str, spreadsheet_id: str, input_data: dict
):
  # TODO: docstring
  cell = get_cell(d, epoch_zero_cell, _EPOCH_ZERO)
  print(description, cell)
  range_name = f"{_SHEET_NAME}!{cell}"
  value = get_values(spreadsheet_id, range_name)
  if value.strip() == "":
    print(f'{description}: NO result!') 
    input_value = input_data.get(description, "")
    stripped_input = input_value.strip()

    if stripped_input == "-":
      print(f'  Detected sentinel "-", skipping update...')
    elif stripped_input == "":
      pass
    else:
      print(f'  Updating with {input_value}')
      update_values(spreadsheet_id, range_name, "USER_ENTERED", [[input_value]])

    value = input_value

  return value
  
def construct_google_application_credentials(
  project_id: str,
  client_email: str,
  client_x509_cert_url: str,
  private_key_id: str, 
  private_key: str,
  client_id: str,
):
  return {
    "type": "service_account",
    "project_id": project_id,
    "private_key_id": private_key_id,
    "private_key": private_key,
    "client_email": client_email,
    "client_id": client_id,
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
    "token_uri": "https://oauth2.googleapis.com/token",
    "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
    "client_x509_cert_url": client_x509_cert_url,
    "universe_domain": "googleapis.com"
  }


async def sheets_get_values(
  telegram_token: str, 
  sheets_spreadsheet_id: str, 
  google_application_credentials_path: str, 
  telegram_write_chat_id: str,
  dry_run: bool = True
):
  os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = google_application_credentials_path  
  SHEETS_SPREADSHEET_ID = sheets_spreadsheet_id
  bot = telegram.Bot(token=telegram_token)

  today = _EST.localize(dt.datetime.now())
  # In UTC, depending on when we trigger this run, we may or may not have
  # to subtract a day. Right now this logic lives in `modal_dispatch.py`
  run_date = today - dt.timedelta(days=1)

  # Get latest (-1) input
  msg = await get_message_by_offset(bot, -1)
  _input = msg.text
  _input_array = _input.split()

  if len(_input_array) == _EXPECTED_INPUT_ARRAY_LEN:
    input_data = {
      tup[0]: tup[1] for tup in zip(_CATEGORIES_ORDER, _input_array)
    }
  else:
    print(f"Got unexpected input {_input}, discarding...")
    input_data = {}

  print(f'input data: {input_data}')

  # Report the latest completed data (yesterday)
  print('For', today, ", running for", run_date)
  results = {}
  for description, epoch_zero_cell in _TODAY_CATEGORIES.items():
    _ = get_or_update_cell(today, description, epoch_zero_cell, SHEETS_SPREADSHEET_ID, input_data)
  
  for description, epoch_zero_cell in _YESTERDAY_CATEGORIES.items():
    results[description] = get_or_update_cell(run_date, description, epoch_zero_cell, SHEETS_SPREADSHEET_ID, input_data)
  
  for description, epoch_zero_cell in _TODAY_CATEGORIES.items():
    # Still want to return yesterday's data for consistency even for `today categories`, 
    # Pass in an empty input_data dict so no updates happen
    results[description] = get_or_update_cell(run_date, description, epoch_zero_cell, SHEETS_SPREADSHEET_ID, input_data={})

  result_str = "\n".join([
    f"{description[0]} {val}" for description, val in results.items()
  ])
  print()
  print(result_str)

  # Try to also recompute data over the last couple of days
  # for i in range(-2, -4, -1):
  #   msg = await get_message_by_offset(bot, i)
  #   print(i, msg)


  # if today.weekday() == 5: # TODO: change to 0 for monday
  #   # Try to recompute data over the last 
  #   for description, epoch_zero_cell in {**_YESTERDAY_CATEGORIES, **_TODAY_CATEGORIES}.items():
  #     cell = get_cell(run_date, epoch_zero_cell, _EPOCH_ZERO)
  #     print(cell)

  message = f"{run_date.day}/{run_date.month}\n{result_str}"
  if dry_run:
    print('\ndry_run=True\n')
    print(message)
  else:
    await send_message(bot, message, telegram_write_chat_id)
    

if __name__ == "__main__":
  token = os.environ['TELEGRAM_TOKEN']
  SHEETS_SPREADSHEET_ID = os.environ['SHEETS_SPREADSHEET_ID']
  write_chat_id = os.environ['TELEGRAM_WRITE_CHAT_ID']
  with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as temp_file:
    if 'GOOGLE_APPLICATION_CREDENTIALS' in os.environ:
      path_to_cleanup = ""
      path = os.environ['GOOGLE_APPLICATION_CREDENTIALS']
    else:
      project_id = "YOUR PROJECT NAME HERE"
      creds = construct_google_application_credentials(
        project_id,
        os.environ['CLIENT_X509_CERT_URL'],
        os.environ['CLIENT_EMAIL'],
        os.environ['PRIVATE_KEY_ID'],
        os.environ['PRIVATE_KEY'].encode().decode('unicode_escape'),
        os.environ['CLIENT_ID'],
      )
      json.dump(creds, temp_file)
      path = temp_file.name
      path_to_cleanup = path
  
  asyncio.run(sheets_get_values(token, SHEETS_SPREADSHEET_ID, path, telegram_write_chat_id=write_chat_id, dry_run=True))
  print(f"Cleaning up {path}")
  os.remove(path_to_cleanup)
    
