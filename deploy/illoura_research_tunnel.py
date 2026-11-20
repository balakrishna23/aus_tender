"""Private local browser access through the existing pinned SSH identity.

Requires paramiko. No credentials are printed; bind only to loopback.
"""
import argparse
from pathlib import Path
import select
import socketserver


def serve(private,port=3030):
    import paramiko
    if not 1024<=port<=65535:raise ValueError('Invalid local port')
    private=Path(private);hosts=paramiko.HostKeys(str(private/'known_hosts'))
    candidates=[h for h in hosts if h.replace('.','').isdigit() and h.count('.')==3]
    if len(candidates)!=1:raise ValueError('Exactly one pinned host required')
    client=paramiko.SSHClient();client.load_host_keys(str(private/'known_hosts'));client.set_missing_host_key_policy(paramiko.RejectPolicy())
    client.connect(candidates[0],username='ubuntu',key_filename=str(private/'admin.pem'),allow_agent=False,look_for_keys=False,timeout=18)
    transport=client.get_transport();transport.set_keepalive(30)

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            channel=transport.open_channel('direct-tcpip',('127.0.0.1',3000),self.client_address)
            try:
                while True:
                    ready,_,_=select.select([self.request,channel],[],[],30)
                    if not transport.is_active():break
                    for source in ready:
                        data=source.recv(65536)
                        if not data:return
                        (channel if source is self.request else self.request).sendall(data)
            finally:channel.close()

    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address=True
        daemon_threads=True

    try:
        with Server(('127.0.0.1',port),Handler) as server:server.serve_forever()
    finally:client.close()


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--private-directory',type=Path,default=Path.home()/'.illoura/aws-trial')
    p.add_argument('--port',type=int,default=3030);args=p.parse_args(argv)
    try:serve(args.private_directory,args.port)
    except KeyboardInterrupt:return 0
    except Exception as exc:print('Research tunnel unavailable: '+type(exc).__name__);return 1


if __name__=='__main__':raise SystemExit(main())
