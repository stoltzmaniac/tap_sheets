#!/usr/bin/env python3

import argparse
import functools
import io
import os
import sys
import json
import logging
import collections
import threading
import http.client
import urllib
import pkg_resources
import time

from jsonschema import validate
import singer
import singer.messages
import singer.metrics as metrics
from singer import utils
from singer import (transform,
                    UNIX_MILLISECONDS_INTEGER_DATETIME_PARSING,
                    Transformer, _transform_datetime)
from singer.catalog import Catalog, CatalogEntry

import httplib2

from apiclient import discovery
from oauth2client import client
from oauth2client import tools
from oauth2client.file import Storage

LOGGER = singer.get_logger()

try:
    parser = argparse.ArgumentParser(parents=[tools.argparser])
    parser.add_argument('-c', '--config', help='Config file', required=True)
    parser.add_argument('-d', '--discover', help='Run in discovery mode', action='store_true')
    flags = parser.parse_args()

except ImportError:
    flags = None
    
import time

def RateLimited(maxPerSecond):
    minInterval = 1.0 / float(maxPerSecond)
    def decorate(func):
        lastTimeCalled = [0.0]
        def rateLimitedFunction(*args,**kargs):
            elapsed = time.clock() - lastTimeCalled[0]
            leftToWait = minInterval - elapsed
            if leftToWait>0:
                time.sleep(leftToWait)
            ret = func(*args,**kargs)
            lastTimeCalled[0] = time.clock()
            return ret
        return rateLimitedFunction
    return decorate


# If modifying these scopes, delete your previously saved credentials
# at ~/.credentials/sheets.googleapis.com-python-quickstart.json
CONFIG = {
    "scopes" : ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive'],
    "client_secret_file" : 'client_secret.json',
    "application_name" : 'Client'
}

def get_credentials():
    """Gets valid user credentials from storage.
    If nothing has been stored, or if the stored credentials are invalid,
    the OAuth2 flow is completed to obtain the new credentials.
    Returns:
        Credentials, the obtained credential.
    """
    
    
    home_dir = os.path.expanduser('~')
    credential_dir = os.path.join(home_dir, '.credentials')
    if not os.path.exists(credential_dir):
        os.makedirs(credential_dir)
    credential_path = os.path.join(credential_dir,
                                   'sheets.googleapis.com-singer-tap.json')

    store = Storage(credential_path)
    credentials = store.get()
    if not credentials or credentials.invalid:
        flow = client.flow_from_clientsecrets(CONFIG['client_secret_file'], CONFIG['scopes'])
        flow.user_agent = CONFIG['application_name']
        if flags:
            credentials = tools.run_flow(flow, store, flags)
        else: # Needed only for compatibility with Python 2.6
            credentials = tools.run(flow, store)
        print('Storing credentials to ' + credential_path)
    return credentials

def do_discover():
    """ Gets sheet information for Docs present in account """
    buildSchema = []
    tempSchema = sheetsList(None)
    nextPageToken = tempSchema.pop("nextPageToken")
    buildSchema = tempSchema["schema_data"]
    while nextPageToken != None:
        tempSchema = sheetsList(nextPageToken)
        nextPageToken = tempSchema.pop("nextPageToken")
        buildSchema.append(tempSchema["schema_data"])
    print(buildSchema)

def sheetsList(pageToken):
    nextPageToken = None
    credentials = get_credentials()
    http = credentials.authorize(httplib2.Http())
    # drive docs - https://developers.google.com/resources/api-libraries/documentation/drive/v3/python/latest/drive_v3.files.html#list
    # sheets docs - https://developers.google.com/resources/api-libraries/documentation/sheets/v4/python/latest/sheets_v4.spreadsheets.values.html#get
    driveService = discovery.build('drive', 'v3', http=http, cache_discovery=False)
    sheetsService = discovery.build('sheets', 'v4', http=http, cache_discovery=False)
    result = driveService.files().list(orderBy=None, q='mimeType=\'application/vnd.google-apps.spreadsheet\'', includeTeamDriveItems=None, pageSize=1000, pageToken=pageToken, corpora=None, supportsTeamDrives=None, spaces=None, teamDriveId=None, corpus=None).execute()
    nextPageToken = result.get('nextPageToken')
    files = result.get('files', [])
    tabList = []
    schema_data = []
    for row in files:
        tabList = tabsInfo(sheetsService, row)
        schema_data = schema_data + tabList
    result = {"schema_data" : schema_data, "nextPageToken" : nextPageToken}
    
    return(result)
    
@RateLimited(1)
def tabsInfo(sheetsService, row):
    result = []
    tabs = sheetsService.spreadsheets().get(
        spreadsheetId=row['id']).execute()
        #spreadsheetId=row['id']).execute()
    for tab_id, tab in enumerate(tabs["sheets"]):
        print(row['id'])
        entry = CatalogEntry(
            tap_stream_id = row['name'].lower().replace(" ", "") + '-' + tab["properties"]["title"].lower().replace(" ",""),
            stream = tab["properties"]["title"].lower().replace(" ", ""),
            database = row['name'].lower().replace(" ", "") + '&' + row['id'],
            table = tab["properties"]["title"].lower().replace(" ", "") + '-' + str(tab_id),
        )
        print(entry)
        result.append(entry)
    return(result)
            
def do_sync(properties):

    credentials = get_credentials()
    http = credentials.authorize(httplib2.Http())
    discoveryUrl = ('https://sheets.googleapis.com/$discovery/rest?'
                    'version=v4')
    service = discovery.build('sheets', 'v4', http=http,
                              discoveryServiceUrl=discoveryUrl, cache_discovery=False)
                              
    spreadsheetId = properties[0]["streams"][0]["tap_stream_id"]
    rangeName = 'A1:D'

    
    result = service.spreadsheets().values().get(
        spreadsheetId=spreadsheetId, range=rangeName, dateTimeRenderOption='FORMATTED_STRING', majorDimension='ROWS').execute()
    values = result.get('values', [])
    header_row = values[0]
    json = []
    if not values:
        print('No data found.')
    else:
        for counter, row in enumerate(values):
            if counter != 0:
                record = {}
                for column_id, value in enumerate(row):
                    record[header_row[column_id]] = row[column_id]
                json.append(record)
    print(json)

def main():
    args = utils.parse_args(
        ["scopes",
         "client_secret_file",
         "application_name"])
    print(args)
    CONFIG.update(args.config)
    STATE = {}

    if args.state:
        STATE.update(args.state)

    if args.discover:
        do_discover()
    elif args.properties:
        do_sync(args.properties)
    else:
        LOGGER.info("No properties were selected")

if __name__ == '__main__':
    main()

