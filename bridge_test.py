import json
import time

import browser_bridge as bridge

bridge.start()
print("Waiting for the Chrome extension to connect...")
while not bridge.connected():
    time.sleep(0.5)
print("Connected!")

print(json.dumps(bridge.request("list_tabs"), indent=1))

text = input("Close tabs matching (for example: youtube). Leave blank to skip: ").strip()
if text:
    print(bridge.request("close_tabs", match=text))