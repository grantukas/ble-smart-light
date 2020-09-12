import asyncio
from bleak import BleakClient
import json
import datetime
from contextlib import AsyncExitStack, asynccontextmanager
from asyncio_mqtt import Client, MqttError
import RPi.GPIO as GPIO

'''
NOTES:

TO CONVERT DEC TO RGB COLOR

def to_rgb(val):
    val1 = hex((val + (1 << 64)) % (1 << 64))[-6:]
    val1 = ':' + ':'.join(str(i) for i in tuple(int(val1[i:i + 2], 16) for i in (0, 2, 4))) + ':'


RGB color picker to ctl

#rgb_color = mesg_payload.replace(' ','').strip('[]').replace(',',':') + ':'
#ctl_str = 'ctl:255:' + rgb_color
'''


# Address of lamp bulb
lamp = 'CA:A9:12:02:DC:01'
wall_light = 'CA:A9:12:02:D9:6F'
addresses = [lamp, wall_light]
client_list = []
reconnect_interval = 3  # [seconds]

# Bluetooth UUID to write RGB ctl values
UUID_WRITE_RGB = '0000ffe1-0000-1000-8000-00805f9b34fb'

#motion = False
timer_sec = 600 # 10 minute timeout
timer = timer_sec
on_off = True
master_key = b'mk:0000'
all_on = b'open'
all_off = b'close'

PIR_PIN = 15
GPIO.setwarnings(False)
GPIO.setmode(GPIO.BCM)
GPIO.setup(PIR_PIN, GPIO.IN) # Input from PIR sensor

# Misc colors
#red = b'ctl:255:255:0:0:'
#green = b'ctl:255:0:255:0:'
#blue = b'ctl:255:0:0:255:'
#white = b'ctl:255:255:255:255:'
#warm_white = b'ctl:255:255:199:44:'
#yellow = b'ctl:255:255:255:0:'
#magenta = b'ctl:255:255:0:255:'


# Convert dec value sent to RGB ctl str
def to_rgb(val, brightness):
    rgb_list = list(int(hex((val + (1 << 64)) % (1 << 64))[-6:][i:i + 2], 16) for i in (0, 2, 4))
    rgb_list[:] = [int(elem * (brightness / 100)) for elem in rgb_list]
    rgb_ctl_str = ':'.join(map(str, rgb_list)) + ':'

    return rgb_ctl_str


# Turn on lights according to PIR sensor
async def motion_timer():
    global timer
    #global motion
    while True: # Countdown every second, poll for motion from PIR
        await asyncio.sleep(1)

        if datetime.datetime.now().hour >= 16: # 19
            if timer > 0:
                timer -= 1
                if GPIO.input(PIR_PIN) and on_off: # If lights are manually turned off, motion will NOT trigger on
                    for client in client_list:
                        await client.write_gatt_char(UUID_WRITE_RGB, bytearray(all_on), True)
                    timer = timer_sec
                    #motion = False
            else:
                if GPIO.input(PIR_PIN) and on_off: # Turn on lights, reset timer
                    for client in client_list:
                        await client.write_gatt_char(UUID_WRITE_RGB, bytearray(all_on), True)
                    timer = timer_sec
                    #motion = False
                else: # Turn off lights, reset timer
                    for client in client_list:
                        await client.write_gatt_char(UUID_WRITE_RGB, bytearray(all_off), True)
                    timer = timer_sec


# Connect to MQTT and listen for messages
async def mqtt_control(id, bleak_client):
    lamp_id = id
    async with AsyncExitStack() as stack:
        # Keep track of the asyncio tasks that we create
        tasks = set()
        stack.push_async_callback(cancel_tasks, tasks)

        # Connect to the MQTT broker
        client = Client('localhost')
        await stack.enter_async_context(client)

        # Filter by topic
        topic_filters = (
            'test/wall',
            'test/lamp',
            'test/onoff',
            'test/allcontrol',
            'test/colorpresets')

        for topic_filter in topic_filters:
            # Log all messages that matches the filter
            manager = client.filtered_messages(topic_filter)
            messages = await stack.enter_async_context(manager)
            template = f'[topic_filter="{topic_filter}"] {{}}'
            task = asyncio.create_task(log_messages(messages, template, lamp_id, bleak_client))
            tasks.add(task)

        # Messages that don't match a filter will get logged here
        messages = await stack.enter_async_context(client.unfiltered_messages())
        task = asyncio.create_task(log_messages(messages, "[unfiltered] {}", lamp_id, bleak_client))
        tasks.add(task)

        # Subscribe to topic(s) after starting message logger
        await client.subscribe('test/#')

        # Wait for everything to complete (or fail due to, e.g., network errors)
        await asyncio.gather(*tasks)


# Log messages, decode and activate lights based on message content
async def log_messages(messages, template, id, client):
    #global motion
    global on_off
    async for message in messages:
        mesg_payload = message.payload.decode()
        formatted_mesg = template.format(mesg_payload)
        #if 'cameraMotionDetector' in mesg_payload: # Toggle motion to true for timer coroutine to activate lights
        #    motion = True
        if 'wall' in formatted_mesg and id == 1: # Wall bulb
            json_data = json.loads(mesg_payload)  # Grab brightness/rgb from json sent
            brightness = json_data['mqtt_dashboard']['brightness']
            rgb_int = json_data['mqtt_dashboard']['color']

            rgb_ctl = to_rgb(rgb_int, brightness)
            ctl_str = 'ctl:255:' + rgb_ctl
            await client.write_gatt_char(UUID_WRITE_RGB, bytearray(ctl_str, 'utf8'), True)
        elif 'lamp' in formatted_mesg and id == 0: # Lamp bulb
            json_data = json.loads(mesg_payload)  # Grab brightness/rgb from json sent
            brightness = json_data['mqtt_dashboard']['brightness']
            rgb_int = json_data['mqtt_dashboard']['color']

            rgb_ctl = to_rgb(rgb_int, brightness)
            ctl_str = 'ctl:255:' + rgb_ctl
            await client.write_gatt_char(UUID_WRITE_RGB, bytearray(ctl_str, 'utf8'), True)
        elif 'onoff' in formatted_mesg: # Toggle lights on/off
            if on_off: # True, turn off
                await client.write_gatt_char(UUID_WRITE_RGB, bytearray(all_off), True)
                on_off = False
            else: # False, turn on
                await client.write_gatt_char(UUID_WRITE_RGB, bytearray(all_on), True)
                on_off = True
        elif 'allcontrol' in formatted_mesg: # Change all light bulb color
            json_data = json.loads(mesg_payload) # Grab brightness/rgb from json sent
            brightness = json_data['mqtt_dashboard']['brightness']
            rgb_int = json_data['mqtt_dashboard']['color']

            rgb_ctl = to_rgb(rgb_int, brightness)
            ctl_str = 'ctl:255:' + rgb_ctl
            await client.write_gatt_char(UUID_WRITE_RGB, bytearray(ctl_str, 'utf8'), True)
        elif 'colorpresets' in formatted_mesg: # Change color based on preset values
            await client.write_gatt_char(UUID_WRITE_RGB, bytearray(mesg_payload, 'utf8'), True)


async def cancel_tasks(tasks):
    for task in tasks:
        if task.done():
            continue
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


# Main method to kick off async coroutines
# Lamp id = 0, wall light = 1
def run(addresses):
    loop = asyncio.get_event_loop()
    for number, address in enumerate(addresses):
        loop.create_task(connect_to_device(address, loop, number))

    # Add motion timer task to run asynchronously
    loop.create_task(motion_timer())
    loop.run_forever()


# Connect to BLE devices via Bleak, then kick off MQTT connection
async def connect_to_device(address, loop, id):
    while True:
        try:
            async with BleakClient(address, loop=loop) as client:
                print("Connected to ", address)
                client_list.append(client)
                await client.write_gatt_char(UUID_WRITE_RGB, bytearray(master_key), True)
                while True:
                    try:
                        await mqtt_control(id, client)
                    except MqttError as error:
                        print(f'Error "{error}". Reconnecting in {reconnect_interval} seconds.')
                    finally:
                        await asyncio.sleep(reconnect_interval)
        except Exception as e:
            print(e)
            print('Trying to reconnect...')


if __name__ == "__main__":
    run([lamp, wall_light])