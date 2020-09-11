import asyncio
from bleak import BleakClient
from asyncio_mqtt import Client
import json
import time
from threading import Timer
import datetime

# Address of lamp bulb
address = 'CA:A9:12:02:DC:01'
wall_light = 'CA:A9:12:02:D9:6F'
addresses = [address, wall_light]
client_list = []

reconnect_interval = 3  # [seconds]

UUID_WRITE_RGB = '0000ffe1-0000-1000-8000-00805f9b34fb'

timer = 10*60 # 10 minute timeout
motion = False
on_off = True
master_key = b'mk:0000'
all_on = b'open'
all_off = b'close'
red = b'ctl:255:255:0:0:'
green = b'ctl:255:0:255:0:'
blue = b'ctl:255:0:0:255:'
white = b'ctl:255:255:255:255:'
warm_white = b'ctl:255:255:199:44:'
yellow = b'ctl:255:255:255:0:'
magenta = b'ctl:255:255:0:255:'

import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from random import randrange
from asyncio_mqtt import Client, MqttError



def to_rgb(val, brightness):
    rgb_list = list(int(hex((val + (1 << 64)) % (1 << 64))[-6:][i:i + 2], 16) for i in (0, 2, 4))
    rgb_list[:] = [int(elem * (brightness / 100)) for elem in rgb_list]
    rgb_ctl_str = ':'.join(map(str, rgb_list)) + ':'

    return rgb_ctl_str


async def motion_timer():
    global timer
    global motion
    while True:
        await asyncio.sleep(1)

        if datetime.datetime.now().hour >= 19:
            if timer > 0:
                timer -= 1
                if motion:
                    print('TIMER NOT OVER, MOTION TRIGGERED')
                    for client in client_list:
                        await client.write_gatt_char(UUID_WRITE_RGB, bytearray(all_on), True)
                    timer = 10
                    motion = False
            else:
                if motion:
                    print('MOTION TRIGGERED, TIME RESET')
                    for client in client_list:
                        await client.write_gatt_char(UUID_WRITE_RGB, bytearray(all_on), True)
                    timer = 10
                    motion = False
                else:
                    print('MOTION TIMEOUT')
                    # Turn off lights, reset timer
                    for client in client_list:
                        await client.write_gatt_char(UUID_WRITE_RGB, bytearray(all_off), True)
                    timer = 10


async def advanced_example(id, bleak_client):
    lamp_id = id
    # We 💛 context managers. Let's create a stack to help
    # us manage them.
    async with AsyncExitStack() as stack:
        # Keep track of the asyncio tasks that we create, so that
        # we can cancel them on exit
        tasks = set()
        stack.push_async_callback(cancel_tasks, tasks)

        # Connect to the MQTT broker
        client = Client('192.168.50.1')
        await stack.enter_async_context(client)

        topic_filters = (
            'test/wall',
            'test/lamp',
            'test/onoff',
            'test/allcontrol',
            'test/colorpresets'
        )
        for topic_filter in topic_filters:
            # Log all messages that matches the filter
            manager = client.filtered_messages(topic_filter)
            messages = await stack.enter_async_context(manager)
            template = f'[topic_filter="{topic_filter}"] {{}}'
            task = asyncio.create_task(log_messages(messages, template, lamp_id, bleak_client))
            tasks.add(task)

        # Messages that doesn't match a filter will get logged here
        messages = await stack.enter_async_context(client.unfiltered_messages())
        task = asyncio.create_task(log_messages(messages, "[unfiltered] {}", lamp_id, bleak_client))
        tasks.add(task)

        # Subscribe to topic(s)
        # 🤔 Note that we subscribe *after* starting the message
        # loggers. Otherwise, we may miss retained messages.
        await client.subscribe('test/#')

        # Wait for everything to complete (or fail due to, e.g., network
        # errors)
        await asyncio.gather(*tasks)

async def log_messages(messages, template, id, client):
    global motion
    global on_off
    async for message in messages:
        # 🤔 Note that we assume that the message paylod is an
        # UTF8-encoded string (hence the `bytes.decode` call).
        mesg_payload = message.payload.decode()
        formatted_mesg = template.format(mesg_payload)
        #print(mesg_payload)
        #print(type(mesg_payload))
        #print(formatted_mesg)
        if 'cameraMotionDetector' in mesg_payload:
            motion = True
            await client.write_gatt_char(UUID_WRITE_RGB, bytearray(all_on), True)
        elif 'wall' in formatted_mesg and id == 1: # Wall bulb
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
        elif 'colorpresets' in formatted_mesg:
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

async def main():
    # Run the advanced_example indefinitely. Reconnect automatically
    # if the connection is lost.
    reconnect_interval = 3  # [seconds]
    while True:
        try:
                await advanced_example()
        except MqttError as error:
            print(f'Error "{error}". Reconnecting in {reconnect_interval} seconds.')
        finally:
            await asyncio.sleep(reconnect_interval)


#asyncio.run(main())


#
#
# BLANK
#
#




async def warm_fade(client, loop):
    await client.write_gatt_char(UUID_WRITE_RGB, bytearray(yellow), True)
    for i in range(255): # Decrement green to make red, 255, 255-i, 0
        fade_out_g = 'ctl:255:255:' + str(255 - i) + ':0:'
        await client.write_gatt_char(UUID_WRITE_RGB, bytearray(fade_out_g, 'utf8'), True)
        await asyncio.sleep(0.05, loop=loop)
    for i in range(64): # Increment blue slightly
        fade_in_b = 'ctl:255:255:0:' + str(i) + ':'
        await client.write_gatt_char(UUID_WRITE_RGB, bytearray(fade_in_b, 'utf8'), True)
        await asyncio.sleep(0.05, loop=loop)
    for i in range(64): # Decrement blue
        fade_out_b = 'ctl:255:255:0:' + str(64-i) + ':'
        await client.write_gatt_char(UUID_WRITE_RGB, bytearray(fade_out_b, 'utf8'), True)
        await asyncio.sleep(0.05, loop=loop)
    for i in range(255): # Increment green to make yellow
        fade_in_g = 'ctl:255:255:' + str(i) + ':0:'
        await client.write_gatt_char(UUID_WRITE_RGB, bytearray(fade_in_g, 'utf8'), True)
        await asyncio.sleep(0.05, loop=loop)
    # End warm fade on yellow

async def cool_fade(client, loop):
    await client.write_gatt_char(UUID_WRITE_RGB, bytearray(magenta), True)
    for i in range(255): # Fade out red completely
        fade_out_r = 'ctl:255:' + str(255 - i) + ':0:255:'
        await client.write_gatt_char(UUID_WRITE_RGB, bytearray(fade_out_r, 'utf8'), True)
        #await asyncio.sleep(0.05, loop=loop)
    for i in range(255): # Increment green
        fade_in_g = 'ctl:255:0:' + str(i) + ':255:'
        await client.write_gatt_char(UUID_WRITE_RGB, bytearray(fade_in_g, 'utf8'), True)
        #await asyncio.sleep(0.05, loop=loop)
    for i in range(255): # Decrement green
        fade_out_g = 'ctl:255:0:' + str(255-i) + ':255:'
        await client.write_gatt_char(UUID_WRITE_RGB, bytearray(fade_out_g, 'utf8'), True)
        #await asyncio.sleep(0.05, loop=loop)
    for i in range(255): # Increment red to make magenta
        fade_in_r = fade_out_r = 'ctl:255:' + str(i) + ':0:255:'
        await client.write_gatt_char(UUID_WRITE_RGB, bytearray(fade_in_r, 'utf8'), True)
        #await asyncio.sleep(0.05, loop=loop)
    # End cool fade on magenta


# Lamp id = 0, wall light = 1
def run(addresses):
    loop = asyncio.get_event_loop()
    for number, address in enumerate(addresses):
        loop.create_task(connect_to_device(address, loop, number))

    loop.create_task(motion_timer())

    loop.run_forever()


async def connect_to_device(address, loop, id):
    while True:
        try:
            async with BleakClient(address, loop=loop) as client:
                print("Connected to ", address)
                client_list.append(client)
                await client.write_gatt_char(UUID_WRITE_RGB, bytearray(master_key), True)
                while True:
                    try:
                        await advanced_example(id, client)
                    except MqttError as error:
                        print(f'Error "{error}". Reconnecting in {reconnect_interval} seconds.')
                    finally:
                        await asyncio.sleep(reconnect_interval)

                    #await warm_fade(client, loop)
                    #await cool_fade(client, loop)
                    await client.write_gatt_char(UUID_WRITE_RGB, bytearray(warm_white), True)
                    #await client.write_gatt_char(UUID_WRITE_RGB, bytearray(red), True)
                    #await asyncio.sleep(0.5, loop=loop)
                    #await client.write_gatt_char(UUID_WRITE_RGB, bytearray(green), True)
                    #await asyncio.sleep(0.5, loop=loop)
                    #await client.write_gatt_char(UUID_WRITE_RGB, bytearray(blue), True)
                    #await asyncio.sleep(0.5, loop=loop)
                    '''await client.write_gatt_char(UUID_WRITE_RGB, bytearray(all_off), True)
                    await asyncio.sleep(1.0, loop=loop)
                    await client.write_gatt_char(UUID_WRITE_RGB, bytearray(all_on), True)
                    await asyncio.sleep(1.0, loop=loop)
                    for i in range(0, 256, 8):
                        # fade_in_red = 'ctl:255:' + str(i) + ':0:255:'
                        fade_in_red = 'ctl:255:' + str(i) + ":0:" + str(255 - i) + ":"
                        await client.write_gatt_char(UUID_WRITE_RGB, bytearray(fade_in_red, 'utf8'), True)
                    await client.write_gatt_char(UUID_WRITE_RGB, bytearray(warm_white), True)
                    await asyncio.sleep(1.0, loop=loop)
                    '''
        except Exception as e:
            print(e)
            print('Trying to reconnect...')

if __name__ == "__main__":
    #loop = asyncio.get_event_loop()
    #loop.run_until_complete(main(address, loop))
    run([address, wall_light])


'''

TO CONVERT DEC TO RGB COLOR

def to_rgb(val):
    val1 = hex((val + (1 << 64)) % (1 << 64))[-6:]
    val1 = ':' + ':'.join(str(i) for i in tuple(int(val1[i:i + 2], 16) for i in (0, 2, 4))) + ':'
    
    
RGB color picker to ctl

#rgb_color = mesg_payload.replace(' ','').strip('[]').replace(',',':') + ':'
#ctl_str = 'ctl:255:' + rgb_color
'''