#!/usr/bin/env python

import sys
import json
import requests
from os.path import relpath, expanduser
import globus_sdk
from globus_sdk.scopes import TransferScopes
from globus_sdk.tokenstorage import SimpleJSONFileAdapter

# ./copydataset.py <dataset name> <destination collection>:<destination folder>
# Example copying the synch dataset. The destination can have any name.
# ./copydataset.py synch 85017645-30ef-4519-abbb-a73811b914b7:/datasets/synch/

CLIENT_ID = "1dc53da9-4f45-43b2-b75f-54368fed256c"
cheapandfair_collection = "7352d991-b0a0-49a2-830c-e8fe8c968ca2"  # collection "Cheap and FAIR Tutorial Datasets"
token_file = '~/.cheapandfair.json'
base_source_path = '/public/datasets/'

if len(sys.argv) != 3:
    print('Incorrect number of arguments')
    print('Usage: ./copydataset.py <dataset name> <destination collection>:<destination folder>')

# Get the name of the dataset to copy from the public samples
dataset = sys.argv[1]
# Get the destination collection and path
dest_srdr_collection, dest_path = sys.argv[2].split(':')
# Assume the destination is a folder
if dest_path[-1] != '/':
    dest_path += '/'
source_path = base_source_path + dataset

# Token scopes for Transfer and the destination HTTPS server
# Scope are different for mapped collections
# See the example in https://github.com/globus/globus-jupyter-notebooks/blob/master/Transfer_API_Exercises.ipynb
SCOPES = [TransferScopes.all,
         f'https://auth.globus.org/scopes/{dest_srdr_collection}/https']

# Cache the tokens locally so you don't need to authenticate every time
token_adapter = SimpleJSONFileAdapter(expanduser(token_file))
if not token_adapter.file_exists():
    native_auth_client = globus_sdk.NativeAppAuthClient(CLIENT_ID)
    native_auth_client.oauth2_start_flow(requested_scopes=SCOPES)
    print(f"Login Here:\n\n{native_auth_client.oauth2_get_authorize_url()}")
    auth_code = input("Please enter the code you get after login here: ").strip()
    print()
    token_response = native_auth_client.oauth2_exchange_code_for_tokens(auth_code)
    token_adapter.store(token_response)
    tokens = token_response.by_resource_server
    transfer_access_token = tokens['transfer.api.globus.org']['access_token']
    https_token = tokens[dest_srdr_collection]['access_token']    
else:
    tokens = token_adapter.get_token_data("transfer.api.globus.org")
    transfer_access_token = tokens['access_token']
    tokens = token_adapter.get_token_data(dest_srdr_collection)
    https_token = tokens['access_token']    

# Create a Transfer client
transfer_authorizer = globus_sdk.AccessTokenAuthorizer(transfer_access_token)
tc = globus_sdk.TransferClient(authorizer=transfer_authorizer)

# Get information about the destination collection
srdr_coll_info = tc.get_endpoint(dest_srdr_collection)
srdr_base_url = srdr_coll_info['https_server']

source_id = cheapandfair_collection
dest_id = dest_srdr_collection

# Create a transfer request to recursively copy the source dataset folder
# and specifying a SHA256 checksum
# This does not exactly match -a, for example it cannot preserve permissions or ownership
tdata = globus_sdk.TransferData(tc, source_id, dest_id,
                                preserve_timestamp=True)
tdata.add_item(source_path, dest_path, recursive=True, checksum_algorithm='sha256')
submit_result = tc.submit_transfer(tdata)
task_id = submit_result['task_id']

print(f"Waiting for {task_id} to complete copying the data")

while not tc.task_wait(task_id, timeout=30):
    print(f"Another 30 seconds went by without {task_id} terminating")

print("Task has finished, creating the manifest")
print()

# Build the manifest by getting the necessary information
# about each file transferred
manifest = []
next_marker=None
while True:
    transfers = tc.get(f"/task/{task_id}/successful_transfers", query_params=dict(marker=next_marker))
    next_marker = transfers['next_marker']
    for t in transfers['DATA']:
        file_entry = {
            'filename': relpath(t['destination_path'], dest_path),
            'length': t['size'],
            'url': srdr_base_url + t['destination_path'],
            t['checksum_algorithm'].lower(): t['checksum']
        }
        manifest.append(file_entry)
    if next_marker is None:
        break

# Define the URL for the manifest file
put_url = f'{srdr_base_url}{dest_path}manifest.json'

# Upload the file and check the results
headers = {'Authorization':'Bearer '+ https_token}
print(f'Uploading (HTTP PUT) manifest.json to {put_url}')
resp = requests.put(put_url, headers=headers, json=manifest, allow_redirects=False)
if not resp.text:
    c = str(resp.status_code)
    print(f'manfiest PUT to {put_url} with status {c}')
    stat = requests.head(put_url,headers=headers, allow_redirects=False)
    print('Manifest file info (HTTP HEAD):')
    for h in 'Content-Length', 'Content-Type':
        v = stat.headers[h]
        print(f'\t{h}: {v}')
else:
    print(f'FAILED PUT to {put_url}')
    print(f'Check permissions on collection at https://app.globus.org/file-manager/collections/{dest_srdr_collection}/sharing')

print()
    
# Write a local copy of the manifest
local_manifest = f'{dataset}-manifest.json'
with open(local_manifest, 'w') as f:
    json.dump(manifest, f, indent=4)

print(f'A copy of the manifest has been stored locally as {local_manifest}')
