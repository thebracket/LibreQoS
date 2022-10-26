from email.policy import default
from ispConfig import UISPbaseURL, uispAuthToken, generatedPNDownloadMbps, generatedPNUploadMbps, uispSuspendedDownloadMbps, uispSuspendedUploadMbps, allowedSubnets, ignoreSubnets, uispCircuitNaming
import requests
import ipaddress

def isInAllowedSubnets(inputIP):
    # Check whether an IP address occurs inside the allowedSubnets list
	isAllowed = False
	if '/' in inputIP:
		inputIP = inputIP.split('/')[0]
	for subnet in allowedSubnets:
		if (ipaddress.ip_address(inputIP) in ipaddress.ip_network(subnet)):
			isAllowed = True
	return isAllowed

def isInIgnoredSubnets(inputIP):
    # Check whether an IP address occurs within the ignoreSubnets list
	isIgnored = False
	if '/' in inputIP:
		inputIP = inputIP.split('/')[0]
	for subnet in ignoreSubnets:
		if (ipaddress.ip_address(inputIP) in ipaddress.ip_network(subnet)):
			isIgnored = True
	return isIgnored

def fixSubnet(inputIP):
    # If an IP address has a CIDR other than /32 (e.g. 192.168.1.1/24), 
    # but doesn't appear as a network address (e.g. 192.168.1.0/24)
    # then it probably isn't actually serving that whole subnet.
    # This allows you to specify e.g. 192.168.1.0/24 is "the client
    # on port 3" in the device, without falling afoul of UISP's inclusion
    # of subnet masks in device IPs.
    [rawIp, cidr] = inputIP.split('/')
    if cidr != "32":
        try:
            subnet = ipaddress.ip_network(inputIP)
        except:
            # Not a network address
            return rawIp + "/32"
    return inputIP

class Device:
    # Identifies a UISP device
    id : str
    name : str
    parent : str
    mac: str
    ipAddresses : list

    def __init__(self, device):
        self.ipAddresses = []
        self.id = device["identification"]["id"]
        self.name = device["identification"]["name"]
        self.parent = device["identification"]["site"]["id"]
        self.mac = device["identification"]["mac"]
        for interface in device["interfaces"]:
            for ip in interface["addresses"]:
                ip = ip["cidr"]
                if isInIgnoredSubnets(ip)==False and isInAllowedSubnets(ip):
                    self.ipAddresses.append(fixSubnet(ip))

    def containsUsefulData(self):
        if len(self.ipAddresses) == 0:
            return False
        return True

    def ipv4ToList(self):
        # Takes a list of IPv4 addresses and turns it into a de-duplicated
        # comma-separated list without a final comma.
        dedupe = list(dict.fromkeys(self.ipAddresses))
        ipList = ""
        for ip in dedupe:
            ipList += ip + ", "
        ipList = ipList[0:len(ipList)-2]
        return ipList

class ClientSite:
    # Identifies a client site
    id : int
    name : str
    address: str
    devices : list
    downloadSpeed : int
    uploadSpeed : int
    suspended : bool

    def __init__(self, client):
        # Creates a ClientSite object from a UISP record
        self.id = client["id"]
        self.name = client["identification"]["name"]
        self.address = client["description"]["address"]
        self.devices = []
        self.ipAddresses = []
        self.suspended = client["identification"]["suspended"]
        if self.suspended:
            self.downloadSpeed = uispSuspendedDownloadMbps
            self.uploadSpeed = uispSuspendedUploadMbps
            self.name += " (s)"
        else:
            if client["qos"]["downloadSpeed"] and client["qos"]["uploadSpeed"]:
                self.downloadSpeed = client["qos"]["downloadSpeed"] / 1000000
                self.uploadSpeed = client["qos"]["uploadSpeed"] / 1000000
            else:
                self.downloadSpeed = generatedPNDownloadMbps
                self.uploadSpeed = generatedPNUploadMbps

    def addDevices(self, deviceList):
        # Parses the UISP devices JSON and extract any devices
        # that are located within the client site.
        for device in deviceList:
            if device["identification"]["site"]["id"] == self.id:
                d = Device(device)
                if d.containsUsefulData():
                    self.devices.append(d)

    def summary(self):
        print(self.id + ": " + self.name + " has " + str(len(self.devices)) + "devices")
        print(self.address)

    def toCircuits(self, circuits, nextCircuitId, nextDeviceId):
        for device in self.devices:
            if device.containsUsefulData():
                c = Circuit()
                c.id = nextCircuitId
                match uispCircuitNaming:
                    case "address": c.name = self.address
                    case "name": c.name = self.name
                    case default: c.name = self.id
                c.deviceId = nextDeviceId # Unsure about device id
                nextDeviceId += 1
                c.deviceName = device.name
                c.parentNode = "" # Fill this in in the future
                c.mac = device.mac
                c.ipv4 = device.ipv4ToList()
                c.ipv6 = "" # To do
                c.uploadMax = self.uploadSpeed
                c.downloadMax = self.downloadSpeed
                c.uploadMin = c.uploadMax * 0.98
                c.downloadMin = c.downloadMax * 0.98
                circuits.append(c)
        return nextDeviceId

class Circuit:
    id : str
    name : str
    deviceId : str
    deviceName : str
    parentNode : str
    mac : str
    ipv4 : str
    ipv6 : str
    uploadMin : int
    uploadMax : int
    downloadMin : int
    downloadMax : int

    def asArray(self) -> list:
        return list([self.id, self.name, self.deviceId, self.deviceName, self.parentNode, self.mac, self.ipv4, self.ipv6, self.uploadMin, self.downloadMin, self.downloadMin, self.downloadMax, ""])

    def summary(self):
        print(self.name + " :" + self.ipv4 + "; " + str(self.downloadMax) + "/" + str(self.uploadMax))
        print(self.deviceName)

def fetchJson(nmsUrl):
    # Fetches data from UISP, including authentication headers.
    url = UISPbaseURL + "/nms/api/v2.1/" + nmsUrl;
    headers = {'accept':'application/json', 'x-auth-token': uispAuthToken}
    r = requests.get(url, headers=headers)
    return r.json()

def loadAllSites():
    # Loads all sites, including client sites from uISP
    return fetchJson("sites?ucrmDetails=true")

def loadClientSitesOnly():
    # Loads all client sites, and not tower sites
    return fetchJson("sites?ucrmDetails=true&type=client")

def loadDevicesWithInterfaces():
    # Loads all devices including interface definitions
    return fetchJson("devices?withInterfaces=true&authorized=true")

def getClientSiteList():
    rawSites = loadClientSitesOnly()
    print("Mapping sites to objects")
    sites = []
    for site in rawSites:
        sites.append(ClientSite(site))
    print("Fetching devices")
    devices = loadDevicesWithInterfaces()
    print("Mapping devices to sites")
    for site in sites:
        site.addDevices(devices)
    return sites

def buildCircuits(clients):
    circuits = []
    circuitId = 0
    deviceId = 0
    for c in clients:
        deviceId = c.toCircuits(circuits, circuitId, deviceId)
        circuitId += 1
    return circuits