"""One-time interactive authorization for independent internal Gmail alerts.

Run manually with an installed-app OAuth client file from Illoura's Google Cloud
project. Never place the client or token file in the public repository.
"""
import argparse
import os
from pathlib import Path

from bdm_monitor import atomic_json


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client',type=Path,required=True)
    parser.add_argument('--token',type=Path,required=True)
    args=parser.parse_args()
    from google_auth_oauthlib.flow import InstalledAppFlow
    # readonly supports account validation and Sent reconciliation after uncertain sends.
    scopes=['https://www.googleapis.com/auth/gmail.send','https://www.googleapis.com/auth/gmail.readonly']
    flow=InstalledAppFlow.from_client_secrets_file(str(args.client),scopes)
    credentials=flow.run_local_server(port=0,open_browser=True,access_type='offline',prompt='consent')
    import json
    atomic_json(args.token,json.loads(credentials.to_json()))
    if os.name!='nt':
        args.token.chmod(0o600)
    print('Saved private authorization file. Account identity will be checked before delivery.')


if __name__=='__main__': main()
