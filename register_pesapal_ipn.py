# Run this ONCE, manually, to register this API's payments webhook with
# PesaPal: `python register_pesapal_ipn.py <public-webhook-url>`
#
# PesaPal doesn't take a callback URL per-payment - you register it once
# and get back an ipn_id, which then gets reused for every single order
# from then on (see pesapal_client.submit_order_request's notification_id).
# Put the printed ipn_id into .env as PESAPAL_IPN_ID.
#
# The URL argument must be PUBLICLY reachable - PesaPal (a real external
# service) can't call http://localhost:8000/... during local development.
# Use a tunnel (e.g. ngrok http 8000) and pass its https URL + the webhook
# path, e.g.:
#   python register_pesapal_ipn.py https://abcd1234.ngrok.io/api/v1/payments/webhook

import sys

from pesapal_client import register_ipn


def main():
    if len(sys.argv) != 2:
        print("Usage: python register_pesapal_ipn.py <public-webhook-url>")
        sys.exit(1)

    webhook_url = sys.argv[1]
    ipn_id = register_ipn(webhook_url, notification_type="GET")
    print(f"Registered IPN URL: {webhook_url}")
    print(f"ipn_id: {ipn_id}")
    print("Add this to .env as PESAPAL_IPN_ID")


if __name__ == "__main__":
    main()
