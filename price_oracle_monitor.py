from datetime import datetime, timedelta
from discord_webhook import DiscordWebhook
from discord_webhook import DiscordEmbed
from pdpyras import EventsAPISession

import argparse
import requests
import json
import logging
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
        "-e",
        "--delay",
        dest="delay",
        type=int,
        default=30,
        required=False,
        help="Time between repeated notifications (default 30 minutes)"
    )
    parser.add_argument(
        "-f",
        "--frequency",
        dest="frequency",
        type=float,
        default=5.0,
        required=False,
        help="The frequency in minutes that CC-POM checks the API for new misses"
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
        "-p",
        "--pagerduty",
        action='store_true',
        help="Enable discord notifications"
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
        "-t",
        "--threshold",
        dest="threshold",
        type=int,
        required=False,
        help="Threshold for wallet balances before sending notifications"
    )
    parser.add_argument(
        "-u",
        "--userid",
        dest="discord_uuid",
        type=str,
        required=False,
        help="Discord User by UUID to tag in alerts"
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action='store_true',
        help="Enable verbose output"
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

    args = parser.parse_args()
    global active_alerts
    global service_list
    service_list=[]
    set_service_list(args)
    active_alerts=[]
    previous_misses={}
    #misses=27

    # Configure Logger
    global logger
    logger = logging.getLogger("price_oracle_monitor")
    if args.verbose:
        logging.basicConfig(format='[%(asctime)s][%(name)22s][%(funcName)20s] %(message)s', level=logging.DEBUG)
    else:
        logging.basicConfig(format='[%(asctime)s][%(name)s] %(message)s', level=logging.INFO)

    #Check valid argument combinations
    if args.threshold and (args.discord_threshold or args.pagerduty_threshold):
        logger.error("Global thresholds cannot be used with service specific thresholds")
        sys.exit()

    # Main service loop
    while True:
        #misses-=1
        for address in args.addresses:
            num_miss_query=f"/oracle/validators/{address}/miss"

            for endpoint in args.lcd_endpoint:
                json_response,status_code=query_lcd(endpoint, num_miss_query)
                if status_code != 200:
                    logger.debug(f"Endpoint: {endpoint}")
                    logger.debug(f"Status Code: {status_code}")
                else:
                    break

            misses=int(json_response['miss_counter'])
            alert_time = datetime.now()

            # Check if the address is being run for the first time OR
            # Check if the address has more misses than prior runs
            if address not in previous_misses.keys() or previous_misses[address] < misses:
                if compare_balance(args.threshold, misses):
                    for service in service_list:
                        manage_service_alerts(address, service, misses, args.delay, num_miss_query, alert_time)
                previous_misses[address]=int(misses)

            # Cleanup stale/timed-out alerts
            elif previous_misses[address] == misses:
                for service in service_list:
                    active_alert = check_active_alerts(active_alerts,address,service['Service'])
                    if active_alert and alert_time >= (active_alert['Last Alert'] + timedelta(minutes=args.delay)):
                        logger.info(f"Cleaning up active alerts: {service['Service']}")
                        delete_active_alert(address,service['Service'])

            # Cleanup alerts from previous ephocs
            elif previous_misses[address] > misses:
                for service in service_list:
                    logger.debug(f"Cleaning up active alerts: {service['Service']}")
                    delete_active_alert(address,service['Service'])

            logger.info(f"Current misses for {address}: {misses}")

        logger.info(f"Active Alerts: {active_alerts}")
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

def create_discord_embed(address, misses, threshold, lcd_endpoint, num_miss_query):
    embed = DiscordEmbed(title="Price Oracle Alert", description=f"{address}\r\n{lcd_endpoint}{num_miss_query}", color='e53935')
    embed.add_embed_field(name='Misses', value=misses)
    embed.add_embed_field(name='Threshold', value=threshold)
    return embed

def check_active_alerts(active_alerts, address, service):
    logger.debug("Checking active_alerts for matching alert")
    logger.debug(f"{active_alerts}")
    if active_alerts:
        for active_alert in active_alerts:
                if active_alert['Service'] == service and active_alert['Address'] == address:
                    return active_alert

def delete_active_alert(address, service):
    logger.debug("Checking active_alerts for matching alert")
    logger.debug(f"{active_alerts}")
    for i in range(len(active_alerts)):
        if active_alerts[i]['Service'] == service and active_alerts[i]['Address'] == address:
            del active_alerts[i]
    logger.debug(f"{active_alerts}")

def check_response(response, service):
    if response.status_code != 200:
        logger.error(f"Error sending alert: {service}")

def set_service_list(args):
    if args.pagerduty or args.pagerduty_api_key:
        if args.threshold:
            pagerduty_threshold=args.threshold
        else:
            pagerduty_threshold=args.pagerduty_threshold
        pagerduty={
            'Service': "PagerDuty",
            'API': args.pagerduty_api_key,
            'Threshold': pagerduty_threshold
        }
        service_list.append(pagerduty)
    if args.discord or args.discord_webhook:
        if args.threshold:
            discord_threshold=args.threshold
        else:
            discord_threshold=args.discord_threshold
        discord={
            'Service': "Discord",
            'API': args.discord_webhook,
            'Threshold': discord_threshold,
            'UUID': args.discord_uuid
        }
        service_list.append(discord)
        
def manage_service_alerts(address, service, misses, delay, num_miss_query, alert_time):
    if service['Service'] == "PagerDuty":
        summary = f"Price Oralce Alert: {address} - {misses} Missed"
        active_alert = check_active_alerts(active_alerts,address,service['Service'])
        if active_alert:
            if alert_time >= (active_alert['Last Alert'] + timedelta(minutes=delay)):
                response = send_pagerduty_alert(service['API'], summary)
                check_response(response,service['Service'])
                # Update 'Last Alert' for the specific address/service pair
                active_alert['Last Alert'] = alert_time
        else:
            # Creates the first alert for the address/service pair
            active_alerts.append(create_alert(service['Service'], address, misses, alert_time))
            response = send_pagerduty_alert(service['API'], summary)
            check_response(response,service['Service'])

    if service['Service'] == "Discord":
        active_alert = check_active_alerts(active_alerts,address,service['Service'])
        embed=create_discord_embed(address, misses, service['Threshold'], service['API'], num_miss_query)
        if active_alert:
            # Time since last alert is greather than or equal to the user set frequency
            if alert_time >= (active_alert['Last Alert'] + timedelta(minutes=delay)):
                response = send_discord_alert(service['API'], service['UUID'], embed)
                check_response(response,service)
                # Update 'Last Alert' for the specific address/service pair
                active_alert['Last Alert'] = alert_time
        else:
            # Creates the first alert for the address/service pair
            active_alerts.append(create_alert(service['Service'], address, misses, alert_time))
            response = send_discord_alert(service['API'], service['UUID'], embed)
            check_response(response,service['Service'])

if __name__ == "__main__":
    main()