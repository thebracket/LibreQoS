# v1.2 (IPv4 + IPv6) (Stable)

<img alt="LibreQoS" src="https://raw.githubusercontent.com/rchac/LibreQoS/main/docs/v1.1-alpha-preview.jpg"></a>

## Installation Guide
- 📄 [LibreQoS v1.2 Installation & Usage Guide Physical Server and Ubuntu 22.04](https://github.com/rchac/LibreQoS/wiki/LibreQoS-v1.2-Installation-&-Usage-Guide-Physical-Server-and-Ubuntu-22.04)

## Features

- Support for multiple devices per subscriber circuit. This allows for multiple IPv4s to be filtered into the same queue, without necessarily being in the same subnet.

- Support for multiple IPv4s or IPv6s per device

- Reduced reload time by 80%. Actual packet loss is <25ms on reload of queues.

- Command line arguments ```--debug```, ```--verbose```, ```--clearrules``` and ```--validate```.

- lqTools.py - ```change-circuit-bandwidth```, ```change-circuit-bandwidth-using-ip```, ```show-active-plan-from-ip```, ```tc-statistics-from-ip```

- Validation of ShapedDevices.csv and network.json during load. If either fails validation, LibreQoS pulls from the last known good configuration (lastGoodConfig.csv and lastGoodConfig.json).

## ShapedDevices.csv
Shaper.csv is now ShapedDevices.csv

New minimums apply to upload and download parameters:

* Download minimum must be 1Mbps or more
* Upload minimum must be 1Mbps or more
* Download maximum must be 2Mbps or more
* Upload maximum must be 2Mbps or more
    
ShapedDevices.csv now has a field for Circuit ID. If the listed Circuit ID is the same between two or more devices, those devices will all be placed into the same queue. If a Circuit ID is not provided for a device, it gets its own circuit. Circuit Name is optional, but recommended. The client's service loction address might be good to use as the Circuit Name.

## IPv6 Support
Full, XDP accelerated made possible by [@thebracket](https://github.com/thebracket)

## UISP Integration
This integration fully maps out your entire UISP network.
Add UISP info under "Optional UISP integration" in ispConfig.py

To use:
1. Delete network.json and, if you have it, integrationUISPbandwidths.csv
2. run ```python3 integrationUISP.py```

It will create a network.json with approximated bandwidths for APs based on UISP's reported capacities, and fixed bandwidth of 1000/1000 for sites.
You can modify integrationUISPbandwidths.csv to correct bandwidth rates. It will load integrationUISPbandwidths.csv on each run and use those listed bandwidths to create network.json. It will always overwrite ShapedDevices.csv on each run by pulling devices from UISP.

### UISP Integration - IPv6 Support
This will match IPv4 MAC addresses in the DHCP server leases of your mikrotik to DHCPv6 bindings, and include those IPv6 addresses with their respective devices.

To enable:
* Edit mikrotikDHCPRouterList.csv to list of your mikrotik DHCPv6 servers
* Set findIPv6usingMikrotik in ispConfig.py to True
