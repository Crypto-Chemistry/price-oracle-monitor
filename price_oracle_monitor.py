from datetime import datetime, timedelta
from discord_webhook import DiscordWebhook
from discord_webhook import DiscordEmbed
from pdpyras import EventsAPISession

import argparse
import requests
import json
import os
import time
import sys

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-a",
        "--addresses",
        nargs='+',
        dest="addresses",
        type=str,
        required=False,
        help="Addresses to monitor balances"
    )
    parser.add_argument(
        "-d",
        "--discord",
        action='store_true',
        help="Enable discord notifications"
    )
    parser.add_argument(
        "-p",
        "--pagerduty",
        action='store_true',
        help="Enable discord notifications"
    )
    parser.add_argument(
        "-w",
        "--webhook",
        dest="discord_webhook",
        type=str,
        required=False,
        help="Discord webhook url to send notifications to"
    )
    parser.add_argument(
        "-t",
        "--threshold",
        dest="threshold",
        type=int,
        required=False,
        help="Threshold for wallet balances before sending notifications"
    )
    parser.add_argument(
        "-r",
        "--list-threshold",
        dest="threshold_list",
        type=list,
        required=False,
        help="Thresholds for wallet balances before sending notifications"
    )
    parser.add_argument(
        "-k",
        "--key",
        dest="pagerduty_api_key",
        type=str,
        required=False,
        help="Pagerduty API Key to send notifications to"
    )
    parser.add_argument(
        "-l",
        "--lcd",
        dest="lcd_endpoint",
        nargs='+',
        type=str,
        required=False,
        help="Node API Endpoint(s) to connect to"
    )
    parser.add_argument(
        "-f",
        "--frequency",
        dest="frequency",
        type=int,
        default=5,
        required=False,
        help="The frequency in minutes that CC-POM checks the API for new misses"
    )
    parser.add_argument(
        "-e",
        "--delay",
        dest="delay",
        type=int,
        default=30,
        required=False,
        help="Time between repeated notifications (default 30 minutes)"
    )
    parser.add_argument(
        "--discord_threshold",
        dest="discord_threshold",
        type=int,
        required=False,
        help="Discord specific threshold for alerts: TODO"
    )
    parser.add_argument(
        "--pagerduty_threshold",
        dest="pagerduty_threshold",
        type=int,
        required=False,
        help="PagerDuty specific threshold for alerts: TODO"
    )
    parser.add_argument(
        "-u",
        "--userid",
        dest="discord_uuid",
        type=str,
        required=False,
        help="Discord User by UUID to tag in alerts"
    )

    args = parser.parse_args()
    active_alerts=[]
    previous_misses={}
    #misses=1
    while True:
        #misses+=1
        for address in args.addresses:
            num_miss_query=f"/oracle/validators/{address}/miss"
            for endpoint in args.lcd_endpoint:
                json_response,status_code=query_lcd(endpoint, num_miss_query)
                if status_code != 200:
                    print("Trying Next Endpoint")
                else:
                    break
            misses=int(json_response['miss_counter'])

            # Check if the address is being run for the first time OR
            # Check if the address has more misses than prior runs
            if address not in previous_misses.keys() or previous_misses[address] < misses:
                if args.threshold and (args.discord_threshold or args.pagerduty_threshold):
                    print("Error: Global thresholds cannot be used with service specific thresholds")
                    sys.exit()
                if compare_balance(args.threshold, misses):
                    alert_time = datetime.now()
                    if args.pagerduty or args.pagerduty_api_key:
                        service = "PagerDuty"
                        summary = f"Price Oralce Alert: {address} - {misses} Missed"
                        active_alert = check_active_alerts(active_alerts,address,service)
                        if active_alert:
                            if alert_time >= (active_alert['Last Alert'] + timedelta(minutes=args.delay)):
                                response = send_pagerduty_alert(args.pagerduty_api_key, summary)
                                check_response(response,service)
                                # Update 'Last Alert' for the specific address/service pair
                                active_alert['Last Alert'] = alert_time
                        else:
                            # Creates the first alert for the address/service pair
                            active_alerts.append(create_alert(service, address, misses, alert_time))
                            response = send_pagerduty_alert(args.pagerduty_api_key, summary)
                            check_response(response,service)
                    if args.discord or args.discord_webhook:
                        service = "Discord"
                        active_alert = check_active_alerts(active_alerts,address,service)
                        embed=create_discord_embed(address, misses, args.threshold, endpoint, num_miss_query)
                        if active_alert:
                            # Time since last alert is greather than or equal to the user set frequency
                            if alert_time >= (active_alert['Last Alert'] + timedelta(minutes=args.delay)):
                                response = send_discord_alert(args.discord_webhook, args.discord_uuid, embed)
                                check_response(response,service)
                                # Update 'Last Alert' for the specific address/service pair
                                active_alert['Last Alert'] = alert_time
                        else:
                            # Creates the first alert for the address/service pair
                            active_alerts.append(create_alert(service, address, misses, alert_time))
                            response = send_discord_alert(args.discord_webhook, args.discord_uuid, embed)
                            check_response(response,service)

                previous_misses[address]=int(misses)
            elif previous_misses[address] == misses:
                for service in "Discord" "PagerDuty":
                    active_alert = check_active_alerts(active_alerts,address,service)
                    if alert_time >= (active_alert['Last Alert'] + timedelta(minutes=args.delay)):
                        delete_active_alert(address,service)
                        print("Cleaning up stale alerts")
            elif previous_misses[address] > misses:
                for service in "Discord" "PagerDuty":
                    delete_active_alert(address,service)
                    print("Cleaning up active_alerts (most likely due to epoch)")
            print(f"Current misses for {address}: {misses}")

        #print(f"Previous Misses: {previous_misses}")
        print(f"Active Alerts: {active_alerts}")
        #print(f"Sleeping for {args.frequency} minutes")
        time.sleep(args.frequency * 60)

def query_lcd(lcd_endpoint, query):
    response = requests.get(lcd_endpoint+query)
    status_code = response.status_code
    json_response= json.loads(response.text)
    return json_response, status_code

def compare_balance(threshold, misses):
    if misses >= threshold:
        return True
    else:
        return False

def create_alert(service, address, misses, time):
    alert={
        "Service":service,
        "Address":address,
        "Misses":misses,
        "Last Alert":time
    }
    return alert

def send_pagerduty_alert(pagerduty_api_key, summary):
    if not pagerduty_api_key:
        pagerduty_api_key = os.environ['PD_API_KEY']
    session = EventsAPISession(pagerduty_api_key)
    session.trigger(summary, 'ccpom')

def create_discord_embed(address, misses, threshold, lcd_endpoint, num_miss_query):
    embed = DiscordEmbed(title="Price Oracle Alert", description=f"{address}\r\n{lcd_endpoint}{num_miss_query}", color='e53935')
    embed.add_embed_field(name='Misses', value=misses)
    embed.add_embed_field(name='Threshold', value=threshold)
    return embed

def send_discord_alert(url, uuid, *args):
    if not url:
        url = os.environ['DISCORD_WEBHOOK']
    if uuid:
        webhook = DiscordWebhook(url=url,content=f"<@{uuid}>")
    else:
        webhook = DiscordWebhook(url=url)
    for embed in args:
        webhook.add_embed(embed)
    response = webhook.execute()
    return response

def check_active_alerts(active_alerts, address, service):
    if active_alerts:
        for active_alert in active_alerts:
                if active_alert['Service'] == service and active_alert['Address'] == address:
                    return active_alert

def delete_active_alert(address, service):
    global active_alerts
    for i in range(len(active_alerts)):
        if active_alerts[i]['Service'] == service and active_alerts[i]['Address'] == address:
            del active_alerts[i]

def check_response(response, service):
    if response.status_code != 200:
        print(f"Error sending Alert: {service}")

if __name__ == "__main__":
    main()