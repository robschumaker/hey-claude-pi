"""Make the secrets for phone commands: a TOTP secret, two random ntfy topics, and a QR code to scan with
Google Authenticator. Prints the .env lines to standard output (append them to the env file on the Pi) and
writes the QR to the path given. Usage: python3 totp-enrol.py /path/to/qr.png   (needs: pip install qrcode)
"""
import base64, secrets, sys, urllib.parse

out = sys.argv[1] if len(sys.argv) > 1 else "hey-claude-authenticator.png"
sec = base64.b32encode(secrets.token_bytes(20)).decode()
cmd = "hc-" + secrets.token_urlsafe(18).replace("_", "x").replace("-", "x")[:24]
rep = "hr-" + secrets.token_urlsafe(18).replace("_", "x").replace("-", "x")[:24]
uri = ("otpauth://totp/" + urllib.parse.quote("Hey Claude:kitchen-pi") + f"?secret={sec}&issuer="
       + urllib.parse.quote("Hey Claude") + "&digits=6&period=30")
import qrcode
qrcode.make(uri).save(out)
print(f"NTFY_SERVER=https://ntfy.sh\nNTFY_COMMAND_TOPIC={cmd}\nNTFY_REPLY_TOPIC={rep}\nNTFY_TOTP_SECRET={sec}")
print(f"# QR code written to {out}; scan it, then delete it", file=sys.stderr)
