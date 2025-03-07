#!/usr/bin/python3
#!/usr/bin/python3
# v1.3

import csv
import io
import ipaddress
import json
import os
import os.path
import subprocess
from subprocess import PIPE, STDOUT
from datetime import datetime, timedelta
import multiprocessing
import warnings
import psutil
import argparse
import logging
import shutil
import binpacking

from ispConfig import fqOrCAKE, upstreamBandwidthCapacityDownloadMbps, upstreamBandwidthCapacityUploadMbps, \
	interfaceA, interfaceB, enableActualShellCommands, useBinPackingToBalanceCPU, \
	runShellCommandsAsSudo, generatedPNDownloadMbps, generatedPNUploadMbps, queuesAvailableOverride

# Automatically account for TCP overhead of plans. For example a 100Mbps plan needs to be set to 109Mbps for the user to ever see that result on a speed test
# Does not apply to nodes of any sort, just endpoint devices
tcpOverheadFactor = 1.09

def shell(command):
	if enableActualShellCommands:
		if runShellCommandsAsSudo:
			command = 'sudo ' + command
		logging.info(command)
		commands = command.split(' ')
		proc = subprocess.Popen(commands, stdout=subprocess.PIPE)
		for line in io.TextIOWrapper(proc.stdout, encoding="utf-8"):  # or another encoding
			if logging.DEBUG <= logging.root.level:
				print(line)
			if ("RTNETLINK answers" in line) or ("We have an error talking to the kernel" in line):
				warnings.warn("Command: '" + command + "' resulted in " + line, stacklevel=2)
	else:
		logging.info(command)

def shellTC(command):
	if enableActualShellCommands:
		print(command)
		commands = command.split(' ')
		proc = subprocess.Popen(commands, stdout=subprocess.PIPE)
		for line in io.TextIOWrapper(proc.stdout, encoding="utf-8"):  # or another encoding
			if logging.DEBUG <= logging.root.level:
				print(line)
			if ("RTNETLINK answers" in line) or ("We have an error talking to the kernel" in line):
				warnings.warn("Command: '" + command + "' resulted in " + line, stacklevel=2)
				raise SystemError("Command: '" + command + "' resulted in " + line)

def checkIfFirstRunSinceBoot():
	if os.path.isfile("lastRun.txt"):
		with open("lastRun.txt", 'r') as file:
			lastRun = datetime.strptime(file.read(), "%d-%b-%Y (%H:%M:%S.%f)")
		systemRunningSince = datetime.fromtimestamp(psutil.boot_time())
		if systemRunningSince > lastRun:
			print("First time run since system boot.")
			return True
		else:
			print("Not first time run since system boot.")
			return False
	else:
		print("First time run since system boot.")
		return True
	
def clearPriorSettings(interfaceA, interfaceB):
	if enableActualShellCommands:
		# Clear tc filter
		shell('tc qdisc delete dev ' + interfaceA + ' root')
		shell('tc qdisc delete dev ' + interfaceB + ' root')
		#shell('tc qdisc delete dev ' + interfaceA)
		#shell('tc qdisc delete dev ' + interfaceB)
		
def tearDown(interfaceA, interfaceB):
	# Full teardown of everything for exiting LibreQoS
	if enableActualShellCommands:
		# Clear IP filters and remove xdp program from interfaces
		result = os.system('./cpumap-pping/src/xdp_iphash_to_cpu_cmdline --clear')
		shell('ip link set dev ' + interfaceA + ' xdp off')
		shell('ip link set dev ' + interfaceB + ' xdp off')
		clearPriorSettings(interfaceA, interfaceB)

def findQueuesAvailable():
	# Find queues and CPU cores available. Use min between those two as queuesAvailable
	if enableActualShellCommands:
		if queuesAvailableOverride == 0:
			queuesAvailable = 0
			path = '/sys/class/net/' + interfaceA + '/queues/'
			directory_contents = os.listdir(path)
			for item in directory_contents:
				if "tx-" in str(item):
					queuesAvailable += 1
			print("NIC queues:\t\t\t" + str(queuesAvailable))
		else:
			queuesAvailable = queuesAvailableOverride
			print("NIC queues (Override):\t\t\t" + str(queuesAvailable))
		cpuCount = multiprocessing.cpu_count()
		print("CPU cores:\t\t\t" + str(cpuCount))
		if queuesAvailable < 2:
			raise SystemError('Only 1 NIC rx/tx queue avaialable. You will need to use a NIC with 2 or more rx/tx queues available.')
		if queuesAvailable < 2:
			raise SystemError('Only 1 CPU core avaialable. You will need to use a CPU with 2 or more CPU cores.')
		queuesAvailable = min(queuesAvailable,cpuCount)
		print("queuesAvailable set to:\t" + str(queuesAvailable))
	else:
		print("As enableActualShellCommands is False, CPU core / queue count has been set to 16")
		logging.info("NIC queues:\t\t\t" + str(16))
		cpuCount = multiprocessing.cpu_count()
		logging.info("CPU cores:\t\t\t" + str(16))
		logging.info("queuesAvailable set to:\t" + str(16))
		queuesAvailable = 16
	return queuesAvailable

def validateNetworkAndDevices():
	# Verify Network.json is valid json
	networkValidatedOrNot = True
	with open('network.json') as file:
		try:
			temporaryVariable = json.load(file) # put JSON-data to a variable
		except json.decoder.JSONDecodeError:
			warnings.warn("network.json is an invalid JSON file", stacklevel=2) # in case json is invalid
			networkValidatedOrNot = False
	if networkValidatedOrNot == True:
		print("network.json passed validation") 
	# Verify ShapedDevices.csv is valid
	devicesValidatedOrNot = True # True by default, switches to false if ANY entry in ShapedDevices.csv fails validation
	rowNum = 2
	with open('ShapedDevices.csv') as csv_file:
		csv_reader = csv.reader(csv_file, delimiter=',')
		#Remove comments if any
		commentsRemoved = []
		for row in csv_reader:
			if not row[0].startswith('#'):
				commentsRemoved.append(row)
		#Remove header
		commentsRemoved.pop(0) 
		seenTheseIPsAlready = []
		for row in commentsRemoved:
			circuitID, circuitName, deviceID, deviceName, ParentNode, mac, ipv4_input, ipv6_input, downloadMin, uploadMin, downloadMax, uploadMax, comment = row
			# Must have circuitID, it's a unique identifier requried for stateful changes to queue structure
			if circuitID == '':
				warnings.warn("No Circuit ID provided in ShapedDevices.csv at row " + str(rowNum), stacklevel=2)
				devicesValidatedOrNot = False
			# Each entry in ShapedDevices.csv can have multiple IPv4s or IPv6s seperated by commas. Split them up and parse each to ensure valid
			ipv4_subnets_and_hosts = []
			ipv6_subnets_and_hosts = []
			if ipv4_input != "":
				try:
					ipv4_input = ipv4_input.replace(' ','')
					if "," in ipv4_input:
						ipv4_list = ipv4_input.split(',')
					else:
						ipv4_list = [ipv4_input]
					for ipEntry in ipv4_list:
						if ipEntry in seenTheseIPsAlready:
							warnings.warn("Provided IPv4 '" + ipEntry + "' in ShapedDevices.csv at row " + str(rowNum) + " is duplicate.", stacklevel=2)
							devicesValidatedOrNot = False
							seenTheseIPsAlready.append(ipEntry)
						else:
							if (type(ipaddress.ip_network(ipEntry)) is ipaddress.IPv4Network) or (type(ipaddress.ip_address(ipEntry)) is ipaddress.IPv4Address):
								ipv4_subnets_and_hosts.extend(ipEntry)
							else:
								warnings.warn("Provided IPv4 '" + ipEntry + "' in ShapedDevices.csv at row " + str(rowNum) + " is not valid.", stacklevel=2)
								devicesValidatedOrNot = False
							seenTheseIPsAlready.append(ipEntry)
				except:
						warnings.warn("Provided IPv4 '" + ipv4_input + "' in ShapedDevices.csv at row " + str(rowNum) + " is not valid.", stacklevel=2)
						devicesValidatedOrNot = False
			if ipv6_input != "":
				try:
					ipv6_input = ipv6_input.replace(' ','')
					if "," in ipv6_input:
						ipv6_list = ipv6_input.split(',')
					else:
						ipv6_list = [ipv6_input]
					for ipEntry in ipv6_list:
						if ipEntry in seenTheseIPsAlready:
							warnings.warn("Provided IPv6 '" + ipEntry + "' in ShapedDevices.csv at row " + str(rowNum) + " is duplicate.", stacklevel=2)
							devicesValidatedOrNot = False
							seenTheseIPsAlready.append(ipEntry)
						else:
							if (type(ipaddress.ip_network(ipEntry)) is ipaddress.IPv6Network) or (type(ipaddress.ip_address(ipEntry)) is ipaddress.IPv6Address):
								ipv6_subnets_and_hosts.extend(ipEntry)
							else:
								warnings.warn("Provided IPv6 '" + ipEntry + "' in ShapedDevices.csv at row " + str(rowNum) + " is not valid.", stacklevel=2)
								devicesValidatedOrNot = False
							seenTheseIPsAlready.append(ipEntry)
				except:
						warnings.warn("Provided IPv6 '" + ipv6_input + "' in ShapedDevices.csv at row " + str(rowNum) + " is not valid.", stacklevel=2)
						devicesValidatedOrNot = False
			try:
				a = int(downloadMin)
				if a < 1:
					warnings.warn("Provided downloadMin '" + downloadMin + "' in ShapedDevices.csv at row " + str(rowNum) + " is < 1 Mbps.", stacklevel=2)
					devicesValidatedOrNot = False
			except:
				warnings.warn("Provided downloadMin '" + downloadMin + "' in ShapedDevices.csv at row " + str(rowNum) + " is not a valid integer.", stacklevel=2)
				devicesValidatedOrNot = False
			try:
				a = int(uploadMin)
				if a < 1:
					warnings.warn("Provided uploadMin '" + uploadMin + "' in ShapedDevices.csv at row " + str(rowNum) + " is < 1 Mbps.", stacklevel=2)
					devicesValidatedOrNot = False
			except:
				warnings.warn("Provided uploadMin '" + uploadMin + "' in ShapedDevices.csv at row " + str(rowNum) + " is not a valid integer.", stacklevel=2)
				devicesValidatedOrNot = False
			try:
				a = int(downloadMax)
				if a < 2:
					warnings.warn("Provided downloadMax '" + downloadMax + "' in ShapedDevices.csv at row " + str(rowNum) + " is < 2 Mbps.", stacklevel=2)
					devicesValidatedOrNot = False
			except:
				warnings.warn("Provided downloadMax '" + downloadMax + "' in ShapedDevices.csv at row " + str(rowNum) + " is not a valid integer.", stacklevel=2)
				devicesValidatedOrNot = False
			try:
				a = int(uploadMax)
				if a < 2:
					warnings.warn("Provided uploadMax '" + uploadMax + "' in ShapedDevices.csv at row " + str(rowNum) + " is < 2 Mbps.", stacklevel=2)
					devicesValidatedOrNot = False
			except:
				warnings.warn("Provided uploadMax '" + uploadMax + "' in ShapedDevices.csv at row " + str(rowNum) + " is not a valid integer.", stacklevel=2)
				devicesValidatedOrNot = False
			
			try:
				if int(downloadMin) > int(downloadMax):
					warnings.warn("Provided downloadMin '" + downloadMin + "' in ShapedDevices.csv at row " + str(rowNum) + " is greater than downloadMax", stacklevel=2)
					devicesValidatedOrNot = False
				if int(uploadMin) > int(uploadMax):
					warnings.warn("Provided uploadMin '" + downloadMin + "' in ShapedDevices.csv at row " + str(rowNum) + " is greater than uploadMax", stacklevel=2)
					devicesValidatedOrNot = False
			except:
				devicesValidatedOrNot = False
			
			rowNum += 1
	if devicesValidatedOrNot == True:
		print("ShapedDevices.csv passed validation")
	else:
		print("ShapedDevices.csv failed validation")
	
	if (devicesValidatedOrNot == True) and (devicesValidatedOrNot == True):
		return True
	else:
		return False

def loadSubscriberCircuits(shapedDevicesFile):
	# Load Subscriber Circuits & Devices
	subscriberCircuits = []
	knownCircuitIDs = []
	counterForCircuitsWithoutParentNodes = 0
	dictForCircuitsWithoutParentNodes = {}
	with open(shapedDevicesFile) as csv_file:
		csv_reader = csv.reader(csv_file, delimiter=',')
		# Remove comments if any
		commentsRemoved = []
		for row in csv_reader:
			if not row[0].startswith('#'):
				commentsRemoved.append(row)
		# Remove header
		commentsRemoved.pop(0)
		for row in commentsRemoved:
			circuitID, circuitName, deviceID, deviceName, ParentNode, mac, ipv4_input, ipv6_input, downloadMin, uploadMin, downloadMax, uploadMax, comment = row
			ipv4_subnets_and_hosts = []
			# Each entry in ShapedDevices.csv can have multiple IPv4s or IPv6s seperated by commas. Split them up and parse each
			if ipv4_input != "":
				ipv4_input = ipv4_input.replace(' ','')
				if "," in ipv4_input:
					ipv4_list = ipv4_input.split(',')
				else:
					ipv4_list = [ipv4_input]
				for ipEntry in ipv4_list:
					ipv4_subnets_and_hosts.append(ipEntry)
			ipv6_subnets_and_hosts = []
			if ipv6_input != "":
				ipv6_input = ipv6_input.replace(' ','')
				if "," in ipv6_input:
					ipv6_list = ipv6_input.split(',')
				else:
					ipv6_list = [ipv6_input]
				for ipEntry in ipv6_list:
					ipv6_subnets_and_hosts.append(ipEntry)
			# If there is something in the circuit ID field
			if circuitID != "":
				# Seen circuit before
				if circuitID in knownCircuitIDs:
					for circuit in subscriberCircuits:
						if circuit['circuitID'] == circuitID:
							if circuit['ParentNode'] != "none":
								if circuit['ParentNode'] != ParentNode:
									errorMessageString = "Device " + deviceName + " with deviceID " + deviceID + " had different Parent Node from other devices of circuit ID #" + circuitID
									raise ValueError(errorMessageString)
							if ((circuit['minDownload'] != round(int(downloadMin)*tcpOverheadFactor))
								or (circuit['minUpload'] != round(int(uploadMin)*tcpOverheadFactor))
								or (circuit['maxDownload'] != round(int(downloadMax)*tcpOverheadFactor))
								or (circuit['maxUpload'] != round(int(uploadMax)*tcpOverheadFactor))):
								warnings.warn("Device " + deviceName + " with ID " + deviceID + " had different bandwidth parameters than other devices on this circuit. Will instead use the bandwidth parameters defined by the first device added to its circuit.", stacklevel=2)
							devicesListForCircuit = circuit['devices']
							thisDevice = 	{
											  "deviceID": deviceID,
											  "deviceName": deviceName,
											  "mac": mac,
											  "ipv4s": ipv4_subnets_and_hosts,
											  "ipv6s": ipv6_subnets_and_hosts,
											  "comment": comment
											}
							devicesListForCircuit.append(thisDevice)
							circuit['devices'] = devicesListForCircuit
				# Have not seen circuit before
				else:
					knownCircuitIDs.append(circuitID)
					if ParentNode == "":
						ParentNode = "none"
					ParentNode = ParentNode.strip()
					deviceListForCircuit = []
					thisDevice = 	{
									  "deviceID": deviceID,
									  "deviceName": deviceName,
									  "mac": mac,
									  "ipv4s": ipv4_subnets_and_hosts,
									  "ipv6s": ipv6_subnets_and_hosts,
									  "comment": comment
									}
					deviceListForCircuit.append(thisDevice)
					thisCircuit = {
					  "circuitID": circuitID,
					  "circuitName": circuitName,
					  "ParentNode": ParentNode,
					  "devices": deviceListForCircuit,
					  "minDownload": round(int(downloadMin)*tcpOverheadFactor),
					  "minUpload": round(int(uploadMin)*tcpOverheadFactor),
					  "maxDownload": round(int(downloadMax)*tcpOverheadFactor),
					  "maxUpload": round(int(uploadMax)*tcpOverheadFactor),
					  "classid": '',
					  "comment": comment
					}
					if thisCircuit['ParentNode'] == 'none':
						thisCircuit['idForCircuitsWithoutParentNodes'] = counterForCircuitsWithoutParentNodes
						dictForCircuitsWithoutParentNodes[counterForCircuitsWithoutParentNodes] = ((round(int(downloadMax)*tcpOverheadFactor))+(round(int(uploadMax)*tcpOverheadFactor))) 
						counterForCircuitsWithoutParentNodes += 1
					subscriberCircuits.append(thisCircuit)
			# If there is nothing in the circuit ID field
			else:
				# Copy deviceName to circuitName if none defined already
				if circuitName == "":
					circuitName = deviceName
				if ParentNode == "":
					ParentNode = "none"
				ParentNode = ParentNode.strip()
				deviceListForCircuit = []
				thisDevice = 	{
								  "deviceID": deviceID,
								  "deviceName": deviceName,
								  "mac": mac,
								  "ipv4s": ipv4_subnets_and_hosts,
								  "ipv6s": ipv6_subnets_and_hosts,
								}
				deviceListForCircuit.append(thisDevice)
				thisCircuit = {
				  "circuitID": circuitID,
				  "circuitName": circuitName,
				  "ParentNode": ParentNode,
				  "devices": deviceListForCircuit,
				  "minDownload": round(int(downloadMin)*tcpOverheadFactor),
				  "minUpload": round(int(uploadMin)*tcpOverheadFactor),
				  "maxDownload": round(int(downloadMax)*tcpOverheadFactor),
				  "maxUpload": round(int(uploadMax)*tcpOverheadFactor),
				  "classid": '',
				  "comment": comment
				}
				if thisCircuit['ParentNode'] == 'none':
					thisCircuit['idForCircuitsWithoutParentNodes'] = counterForCircuitsWithoutParentNodes
					dictForCircuitsWithoutParentNodes[counterForCircuitsWithoutParentNodes] = ((round(int(downloadMax)*tcpOverheadFactor))+(round(int(uploadMax)*tcpOverheadFactor)))
					counterForCircuitsWithoutParentNodes += 1
				subscriberCircuits.append(thisCircuit)
	return (subscriberCircuits,	dictForCircuitsWithoutParentNodes)

def refreshShapers():
	
	# Starting
	print("refreshShapers starting at " + datetime.now().strftime("%d/%m/%Y %H:%M:%S"))
	
	
	# Warn user if enableActualShellCommands is False, because that would mean no actual commands are executing
	if enableActualShellCommands == False:
		warnings.warn("enableActualShellCommands is set to False. None of the commands below will actually be executed. Simulated run.", stacklevel=2)
	
	
	# Check if first run since boot
	isThisFirstRunSinceBoot = checkIfFirstRunSinceBoot()
	
	
	# Files
	shapedDevicesFile = 'ShapedDevices.csv'
	networkJSONfile = 'network.json'
	
	
	# Check validation
	safeToRunRefresh = False
	print("Validating input files '" + shapedDevicesFile + "' and '" + networkJSONfile + "'")
	if (validateNetworkAndDevices() == True):
		shutil.copyfile('ShapedDevices.csv', 'lastGoodConfig.csv')
		shutil.copyfile('network.json', 'lastGoodConfig.json')
		print("Backed up good config as lastGoodConfig.csv and lastGoodConfig.json")
		safeToRunRefresh = True
	else:
		if (isThisFirstRunSinceBoot == False):
			warnings.warn("Validation failed. Because this is not the first run since boot (queues already set up) - will now exit.", stacklevel=2)
			safeToRunRefresh = False
		else:
			warnings.warn("Validation failed. However - because this is the first run since boot - will load queues from last good config", stacklevel=2)
			shapedDevicesFile = 'lastGoodConfig.csv'
			networkJSONfile = 'lastGoodConfig.json'
			safeToRunRefresh = True
	
	if safeToRunRefresh == True:
		
		# Load Subscriber Circuits & Devices
		subscriberCircuits,	dictForCircuitsWithoutParentNodes = loadSubscriberCircuits(shapedDevicesFile)
		

		# Load network heirarchy
		with open(networkJSONfile, 'r') as j:
			network = json.loads(j.read())
		
		
		# Pull rx/tx queues / CPU cores available
		queuesAvailable = findQueuesAvailable()

		# Generate Parent Nodes. Spread ShapedDevices.csv which lack defined ParentNode across these (balance across CPUs)
		print("Generating parent nodes")
		existingPNs = 0
		for node in network:
			existingPNs += 1
		generatedPNs = []
		numberOfGeneratedPNs = queuesAvailable-existingPNs
		for x in range(numberOfGeneratedPNs):
			genPNname = "Generated_PN_" + str(x+1)
			network[genPNname] =	{
										"downloadBandwidthMbps":generatedPNDownloadMbps,
										"uploadBandwidthMbps":generatedPNUploadMbps
									}
			generatedPNs.append(genPNname)
		if useBinPackingToBalanceCPU:
			print("Using binpacking module to sort circuits by CPU core")
			bins = binpacking.to_constant_bin_number(dictForCircuitsWithoutParentNodes, numberOfGeneratedPNs)
			genPNcounter = 0
			for binItem in bins:
				sumItem = 0
				logging.info(generatedPNs[genPNcounter] + " will contain " + str(len(binItem)) + " circuits")
				for key in binItem.keys():
					for circuit in subscriberCircuits:
						if circuit['ParentNode'] == 'none':
							if circuit['idForCircuitsWithoutParentNodes'] == key:
								circuit['ParentNode'] = generatedPNs[genPNcounter]
				genPNcounter += 1
				if genPNcounter >= queuesAvailable:
					genPNcounter = 0
			print("Finished binpacking")
		else:
			genPNcounter = 0
			for circuit in subscriberCircuits:
				if circuit['ParentNode'] == 'none':
					circuit['ParentNode'] = generatedPNs[genPNcounter]
					genPNcounter += 1
					if genPNcounter >= queuesAvailable:
						genPNcounter = 0
		print("Generated parent nodes created")
		
		# Find the bandwidth minimums for each node by combining mimimums of devices lower in that node's heirarchy
		def findBandwidthMins(data, depth):
			tabs = '   ' * depth
			minDownload = 0
			minUpload = 0
			for elem in data:
				for circuit in subscriberCircuits:
					if elem == circuit['ParentNode']:
						minDownload += circuit['minDownload']
						minUpload += circuit['minUpload']
				if 'children' in data[elem]:
					minDL, minUL = findBandwidthMins(data[elem]['children'], depth+1)
					minDownload += minDL
					minUpload += minUL
				data[elem]['downloadBandwidthMbpsMin'] = minDownload
				data[elem]['uploadBandwidthMbpsMin'] = minUpload
			return minDownload, minUpload
		logging.info("Finding the bandwidth minimums for each node")
		minDownload, minUpload = findBandwidthMins(network, 0)
		logging.info("Found the bandwidth minimums for each node")
		
		# Parse network structure and add devices from ShapedDevices.csv
		parentNodes = []
		minorByCPUpreloaded = {}
		knownClassIDs = []
		# Track minor counter by CPU. This way we can have > 32000 hosts (htb has u16 limit to minor handle)
		for x in range(queuesAvailable):
			minorByCPUpreloaded[x+1] = 3
		def traverseNetwork(data, depth, major, minorByCPU, queue, parentClassID, parentMaxDL, parentMaxUL):
			for node in data:
				circuitsForThisNetworkNode = []
				nodeClassID = hex(major) + ':' + hex(minorByCPU[queue])
				data[node]['classid'] = nodeClassID
				if depth == 0:
					parentClassID = hex(major) + ':'
				data[node]['parentClassID'] = parentClassID
				# Cap based on this node's max bandwidth, or parent node's max bandwidth, whichever is lower
				data[node]['downloadBandwidthMbps'] = min(data[node]['downloadBandwidthMbps'],parentMaxDL)
				data[node]['uploadBandwidthMbps'] = min(data[node]['uploadBandwidthMbps'],parentMaxUL)
				# Calculations are done in findBandwidthMins(), determine optimal HTB rates (mins) and ceils (maxs)
				# For some reason that doesn't always yield the expected result, so it's better to play with ceil more than rate
				# Here we override the rate as 95% of ceil.
				data[node]['downloadBandwidthMbpsMin'] = round(data[node]['downloadBandwidthMbps']*.95)
				data[node]['uploadBandwidthMbpsMin'] = round(data[node]['uploadBandwidthMbps']*.95)
				data[node]['classMajor'] = hex(major)
				data[node]['classMinor'] = hex(minorByCPU[queue])
				data[node]['cpuNum'] = hex(queue-1)
				thisParentNode =	{
									"parentNodeName": node,
									"classID": nodeClassID,
									"maxDownload": data[node]['downloadBandwidthMbps'],
									"maxUpload": data[node]['uploadBandwidthMbps'],
									}
				parentNodes.append(thisParentNode)
				minorByCPU[queue] = minorByCPU[queue] + 1
				for circuit in subscriberCircuits:
					#If a device from ShapedDevices.csv lists this node as its Parent Node, attach it as a leaf to this node HTB
					if node == circuit['ParentNode']:
						if circuit['maxDownload'] > data[node]['downloadBandwidthMbps']:
							warnings.warn("downloadMax of Circuit ID [" + circuit['circuitID'] + "] exceeded that of its parent node. Reducing to that of its parent node now.", stacklevel=2)
						if circuit['maxUpload'] > data[node]['uploadBandwidthMbps']:
							warnings.warn("uploadMax of Circuit ID [" + circuit['circuitID'] + "] exceeded that of its parent node. Reducing to that of its parent node now.", stacklevel=2)
						parentString = hex(major) + ':'
						flowIDstring = hex(major) + ':' + hex(minorByCPU[queue])
						circuit['classid'] = flowIDstring
						# Create circuit dictionary to be added to network structure, eventually output as queuingStructure.json
						maxDownload = min(circuit['maxDownload'],data[node]['downloadBandwidthMbps'])
						maxUpload = min(circuit['maxUpload'],data[node]['uploadBandwidthMbps'])
						minDownload = min(circuit['minDownload'],maxDownload)
						minUpload = min(circuit['minUpload'],maxUpload)
						thisNewCircuitItemForNetwork = {
							'maxDownload' : maxDownload,
							'maxUpload' : maxUpload,
							'minDownload' : minDownload,
							'minUpload' : minUpload,
							"circuitID": circuit['circuitID'],
							"circuitName": circuit['circuitName'],
							"ParentNode": circuit['ParentNode'],
							"devices": circuit['devices'],
							"classid": flowIDstring,
							"classMajor": hex(major),
							"classMinor": hex(minorByCPU[queue]),
							"comment": circuit['comment']
						}
						# Generate TC commands to be executed later
						thisNewCircuitItemForNetwork['devices'] = circuit['devices']
						circuitsForThisNetworkNode.append(thisNewCircuitItemForNetwork)
						minorByCPU[queue] = minorByCPU[queue] + 1
				if len(circuitsForThisNetworkNode) > 0:
					data[node]['circuits'] = circuitsForThisNetworkNode
				# Recursive call this function for children nodes attached to this node
				if 'children' in data[node]:
					# We need to keep tabs on the minor counter, because we can't have repeating class IDs. Here, we bring back the minor counter from the recursive function
					minorByCPU[queue] = minorByCPU[queue] + 1
					minorByCPU = traverseNetwork(data[node]['children'], depth+1, major, minorByCPU, queue, nodeClassID, data[node]['downloadBandwidthMbps'], data[node]['uploadBandwidthMbps'])
				# If top level node, increment to next queue / cpu core
				if depth == 0:
					if queue >= queuesAvailable:
						queue = 1
						major = queue
					else:
						queue += 1
						major += 1
			return minorByCPU
		# Here is the actual call to the recursive traverseNetwork() function. finalMinor is not used.
		minorByCPU = traverseNetwork(network, 0, major=1, minorByCPU=minorByCPUpreloaded, queue=1, parentClassID=None, parentMaxDL=upstreamBandwidthCapacityDownloadMbps, parentMaxUL=upstreamBandwidthCapacityUploadMbps)
		
		
		linuxTCcommands = []
		xdpCPUmapCommands = []
		devicesShaped = []
		# Root HTB Setup
		# Create MQ qdisc for each CPU core / rx-tx queue. Generate commands to create corresponding HTB and leaf classes. Prepare commands for execution later
		thisInterface = interfaceA
		logging.info("# MQ Setup for " + thisInterface)
		command = 'qdisc replace dev ' + thisInterface + ' root handle 7FFF: mq'
		linuxTCcommands.append(command)
		for queue in range(queuesAvailable):
			command = 'qdisc add dev ' + thisInterface + ' parent 7FFF:' + hex(queue+1) + ' handle ' + hex(queue+1) + ': htb default 2'
			linuxTCcommands.append(command)
			command = 'class add dev ' + thisInterface + ' parent ' + hex(queue+1) + ': classid ' + hex(queue+1) + ':1 htb rate '+ str(upstreamBandwidthCapacityDownloadMbps) + 'mbit ceil ' + str(upstreamBandwidthCapacityDownloadMbps) + 'mbit'
			linuxTCcommands.append(command)
			command = 'qdisc add dev ' + thisInterface + ' parent ' + hex(queue+1) + ':1 ' + fqOrCAKE
			linuxTCcommands.append(command)
			# Default class - traffic gets passed through this limiter with lower priority if it enters the top HTB without a specific class.
			# Technically, that should not even happen. So don't expect much if any traffic in this default class.
			# Only 1/4 of defaultClassCapacity is guarenteed (to prevent hitting ceiling of upstream), for the most part it serves as an "up to" ceiling.
			command = 'class add dev ' + thisInterface + ' parent ' + hex(queue+1) + ':1 classid ' + hex(queue+1) + ':2 htb rate ' + str(round((upstreamBandwidthCapacityDownloadMbps-1)/4)) + 'mbit ceil ' + str(upstreamBandwidthCapacityDownloadMbps-1) + 'mbit prio 5'
			linuxTCcommands.append(command)
			command = 'qdisc add dev ' + thisInterface + ' parent ' + hex(queue+1) + ':2 ' + fqOrCAKE
			linuxTCcommands.append(command)
		
		thisInterface = interfaceB
		logging.info("# MQ Setup for " + thisInterface)
		command = 'qdisc replace dev ' + thisInterface + ' root handle 7FFF: mq'
		linuxTCcommands.append(command)
		for queue in range(queuesAvailable):
			command = 'qdisc add dev ' + thisInterface + ' parent 7FFF:' + hex(queue+1) + ' handle ' + hex(queue+1) + ': htb default 2'
			linuxTCcommands.append(command)
			command = 'class add dev ' + thisInterface + ' parent ' + hex(queue+1) + ': classid ' + hex(queue+1) + ':1 htb rate '+ str(upstreamBandwidthCapacityUploadMbps) + 'mbit ceil ' + str(upstreamBandwidthCapacityUploadMbps) + 'mbit'
			linuxTCcommands.append(command)
			command = 'qdisc add dev ' + thisInterface + ' parent ' + hex(queue+1) + ':1 ' + fqOrCAKE
			linuxTCcommands.append(command)
			# Default class - traffic gets passed through this limiter with lower priority if it enters the top HTB without a specific class.
			# Technically, that should not even happen. So don't expect much if any traffic in this default class.
			# Only 1/4 of defaultClassCapacity is guarenteed (to prevent hitting ceiling of upstream), for the most part it serves as an "up to" ceiling.
			command = 'class add dev ' + thisInterface + ' parent ' + hex(queue+1) + ':1 classid ' + hex(queue+1) + ':2 htb rate ' + str(round((upstreamBandwidthCapacityUploadMbps-1)/4)) + 'mbit ceil ' + str(upstreamBandwidthCapacityUploadMbps-1) + 'mbit prio 5'
			linuxTCcommands.append(command)
			command = 'qdisc add dev ' + thisInterface + ' parent ' + hex(queue+1) + ':2 ' + fqOrCAKE
			linuxTCcommands.append(command)
		
		
		# Parse network structure. For each tier, generate commands to create corresponding HTB and leaf classes. Prepare commands for execution later
		# Define lists for hash filters
		def traverseNetwork(data):
			for node in data:
				command = 'class add dev ' + interfaceA + ' parent ' + data[node]['parentClassID'] + ' classid ' + data[node]['classMinor'] + ' htb rate '+ str(data[node]['downloadBandwidthMbpsMin']) + 'mbit ceil '+ str(data[node]['downloadBandwidthMbps']) + 'mbit prio 3'
				linuxTCcommands.append(command)
				command = 'class add dev ' + interfaceB + ' parent ' + data[node]['parentClassID'] + ' classid ' + data[node]['classMinor'] + ' htb rate '+ str(data[node]['uploadBandwidthMbpsMin']) + 'mbit ceil '+ str(data[node]['uploadBandwidthMbps']) + 'mbit prio 3'
				linuxTCcommands.append(command)
				if 'circuits' in data[node]:
					for circuit in data[node]['circuits']:
						# Generate TC commands to be executed later
						comment = " # CircuitID: " + circuit['circuitID'] + " DeviceIDs: "
						for device in circuit['devices']:
							comment = comment + device['deviceID'] + ', '
						if 'devices' in circuit:
							if 'comment' in circuit['devices'][0]:
								comment = comment + '| Comment: ' + circuit['devices'][0]['comment']
						command = 'class add dev ' + interfaceA + ' parent ' + data[node]['classid'] + ' classid ' + circuit['classMinor'] + ' htb rate '+ str(circuit['minDownload']) + 'mbit ceil '+ str(circuit['maxDownload']) + 'mbit prio 3'
						linuxTCcommands.append(command)
						command = 'qdisc add dev ' + interfaceA + ' parent ' + circuit['classMajor'] + ':' + circuit['classMinor'] + ' ' + fqOrCAKE
						linuxTCcommands.append(command)
						command = 'class add dev ' + interfaceB + ' parent ' + data[node]['classid'] + ' classid ' + circuit['classMinor'] + ' htb rate '+ str(circuit['minUpload']) + 'mbit ceil '+ str(circuit['maxUpload']) + 'mbit prio 3'
						linuxTCcommands.append(command)
						command = 'qdisc add dev ' + interfaceB + ' parent ' + circuit['classMajor'] + ':' + circuit['classMinor'] + ' ' + fqOrCAKE
						linuxTCcommands.append(command)
						for device in circuit['devices']:
							if device['ipv4s']:
								for ipv4 in device['ipv4s']:
									xdpCPUmapCommands.append('./cpumap-pping/src/xdp_iphash_to_cpu_cmdline --add --ip ' + str(ipv4) + ' --cpu ' + data[node]['cpuNum'] + ' --classid ' + circuit['classid'])
							if device['ipv6s']:
								for ipv6 in device['ipv6s']:
									xdpCPUmapCommands.append('./cpumap-pping/src/xdp_iphash_to_cpu_cmdline --add --ip ' + str(ipv6) + ' --cpu ' + data[node]['cpuNum'] + ' --classid ' + circuit['classid'])
							if device['deviceName'] not in devicesShaped:
								devicesShaped.append(device['deviceName'])
				# Recursive call this function for children nodes attached to this node
				if 'children' in data[node]:
					traverseNetwork(data[node]['children'])
		# Here is the actual call to the recursive traverseNetwork() function.
		traverseNetwork(network)
		
		# Save queuingStructure
		queuingStructure = {}
		queuingStructure['Network'] = network
		queuingStructure['lastUsedClassIDCounterByCPU'] = minorByCPU
		queuingStructure['generatedPNs'] = generatedPNs
		with open('queuingStructure.json', 'w') as infile:
			json.dump(queuingStructure, infile, indent=4)
		
		
		# Record start time of actual filter reload
		reloadStartTime = datetime.now()
		
		
		# Clear Prior Settings
		clearPriorSettings(interfaceA, interfaceB)

		
		# Setup XDP and disable XPS regardless of whether it is first run or not (necessary to handle cases where systemctl stop was used)
		xdpStartTime = datetime.now()
		if enableActualShellCommands:
			# Here we use os.system for the command, because otherwise it sometimes gltiches out with Popen in shell()
			result = os.system('./cpumap-pping/src/xdp_iphash_to_cpu_cmdline --clear')
		# Set up XDP-CPUMAP-TC
		logging.info("# XDP Setup")
		shell('./cpumap-pping/bin/xps_setup.sh -d ' + interfaceA + ' --default --disable')
		shell('./cpumap-pping/bin/xps_setup.sh -d ' + interfaceB + ' --default --disable')
		shell('./cpumap-pping/src/xdp_iphash_to_cpu --dev ' + interfaceA + ' --lan')
		shell('./cpumap-pping/src/xdp_iphash_to_cpu --dev ' + interfaceB + ' --wan')
		shell('./cpumap-pping/src/tc_classify --dev-egress ' + interfaceA)
		shell('./cpumap-pping/src/tc_classify --dev-egress ' + interfaceB)	
		xdpEndTime = datetime.now()
		
		
		# Execute actual Linux TC commands
		tcStartTime = datetime.now()
		print("Executing linux TC class/qdisc commands")
		with open('linux_tc.txt', 'w') as f:
			for command in linuxTCcommands:
				logging.info(command)
				f.write(f"{command}\n")
		if logging.DEBUG <= logging.root.level:
			# Do not --force in debug mode, so we can see any errors 
			shell("/sbin/tc -b linux_tc.txt")
		else:
			shell("/sbin/tc -f -b linux_tc.txt")
		tcEndTime = datetime.now()
		print("Executed " + str(len(linuxTCcommands)) + " linux TC class/qdisc commands")
				
		
		tcEndTime = datetime.now()
		print("Executed " + str(len(linuxTCcommands)) + " linux TC class/qdisc commands")
		
		
		# Execute actual XDP-CPUMAP-TC filter commands
		xdpFilterStartTime = datetime.now()
		print("Executing XDP-CPUMAP-TC IP filter commands")
		if enableActualShellCommands:
			for command in xdpCPUmapCommands:
				logging.info(command)
				commands = command.split(' ')
				proc = subprocess.Popen(commands, stdout=subprocess.DEVNULL)
		else:
			for command in xdpCPUmapCommands:
				logging.info(command)
		print("Executed " + str(len(xdpCPUmapCommands)) + " XDP-CPUMAP-TC IP filter commands")
		xdpFilterEndTime = datetime.now()
		
		
		# Record end time of all reload commands
		reloadEndTime = datetime.now()
		
		
		# Recap - warn operator if devices were skipped
		devicesSkipped = []
		for circuit in subscriberCircuits:
			for device in circuit['devices']:
				if device['deviceName'] not in devicesShaped:
					devicesSkipped.append((device['deviceName'],device['deviceID']))
		if len(devicesSkipped) > 0:
			warnings.warn('Some devices were not shaped. Please check to ensure they have a valid ParentNode listed in ShapedDevices.csv:', stacklevel=2)
			print("Devices not shaped:")
			for entry in devicesSkipped:
				name, idNum = entry
				print('DeviceID: ' + idNum + '\t DeviceName: ' + name)
		
		# Save ShapedDevices.csv as ShapedDevices.lastLoaded.csv
		shutil.copyfile('ShapedDevices.csv', 'ShapedDevices.lastLoaded.csv')
		
		# Save for stats
		with open('statsByCircuit.json', 'w') as f:
			f.write(json.dumps(subscriberCircuits, indent=4))
		with open('statsByParentNode.json', 'w') as f:
			f.write(json.dumps(parentNodes, indent=4))
		
		
		# Record time this run completed at
		# filename = os.path.join(_here, 'lastRun.txt')
		with open("lastRun.txt", 'w') as file:
			file.write(datetime.now().strftime("%d-%b-%Y (%H:%M:%S.%f)"))
		
		
		# Report reload time
		reloadTimeSeconds = ((reloadEndTime - reloadStartTime).seconds) + (((reloadEndTime - reloadStartTime).microseconds) / 1000000)
		tcTimeSeconds = ((tcEndTime - tcStartTime).seconds) + (((tcEndTime - tcStartTime).microseconds) / 1000000)
		xdpSetupTimeSeconds = ((xdpEndTime - xdpStartTime).seconds) + (((xdpEndTime - xdpStartTime).microseconds) / 1000000)
		xdpFilterTimeSeconds = ((xdpFilterEndTime - xdpFilterStartTime).seconds) + (((xdpFilterEndTime - xdpFilterStartTime).microseconds) / 1000000)
		print("Queue and IP filter reload completed in " + "{:g}".format(round(reloadTimeSeconds,1)) + " seconds")
		print("\tTC commands: \t" + "{:g}".format(round(tcTimeSeconds,1)) + " seconds")
		print("\tXDP setup: \t " + "{:g}".format(round(xdpSetupTimeSeconds,1)) + " seconds")
		print("\tXDP filters: \t " + "{:g}".format(round(xdpFilterTimeSeconds,1)) + " seconds")
		
		
		# Done
		print("refreshShapers completed on " + datetime.now().strftime("%d/%m/%Y %H:%M:%S"))

def refreshShapersUpdateOnly():
	# Starting
	print("refreshShapers starting at " + datetime.now().strftime("%d/%m/%Y %H:%M:%S"))
	
	
	# Warn user if enableActualShellCommands is False, because that would mean no actual commands are executing
	if enableActualShellCommands == False:
		warnings.warn("enableActualShellCommands is set to False. None of the commands below will actually be executed. Simulated run.", stacklevel=2)
	
	
	# Files
	shapedDevicesFile = 'ShapedDevices.csv'
	networkJSONfile = 'network.json'
	
	
	# Check validation
	safeToRunRefresh = False
	if (validateNetworkAndDevices() == True):
		shutil.copyfile('ShapedDevices.csv', 'lastGoodConfig.csv')
		shutil.copyfile('network.json', 'lastGoodConfig.json')
		print("Backed up good config as lastGoodConfig.csv and lastGoodConfig.json")
		safeToRunRefresh = True
	else:
		warnings.warn("Validation failed. Will now exit.", stacklevel=2)
	
	if safeToRunRefresh == True:
		
		# Load queuingStructure
		with open('queuingStructure.json', 'r') as infile:
			queuingStructure = json.loads(infile.read())
		
		
		# Load queuingStructure
		with open('queuingStructure.json', 'r') as infile:
			queuingStructure = json.loads(infile.read())
		
		network = queuingStructure['Network']
		lastUsedClassIDCounterByCPU = queuingStructure['lastUsedClassIDCounterByCPU']
		generatedPNs = queuingStructure['generatedPNs']

		newlyUpdatedSubscriberCircuits,	newlyUpdatedDictForCircuitsWithoutParentNodes = loadSubscriberCircuits('ShapedDevices.csv')					
		lastLoadedSubscriberCircuits, lastLoadedDictForCircuitsWithoutParentNodes = loadSubscriberCircuits('ShapedDevices.lastLoaded.csv')		
		
		
		# Load stats files
		with open('statsByParentNode.json', 'r') as j:
			parentNodes = json.loads(j.read())

		with open('statsByCircuit.json', 'r') as j:
			subscriberCircuits = json.loads(j.read())
		
		newlyUpdatedSubscriberCircuitsByID = {}
		for circuit in newlyUpdatedSubscriberCircuits:
			circuitid = circuit['circuitID']
			newlyUpdatedSubscriberCircuitsByID[circuitid] = circuit
		
		lastLoadedSubscriberCircuitsByID = {}
		for circuit in lastLoadedSubscriberCircuits:
			circuitid = circuit['circuitID']
			lastLoadedSubscriberCircuitsByID[circuitid] = circuit
		
		
		def removeDeviceIPsFromFilter(circuit):
			for device in circuit['devices']:
				for ipv4 in device['ipv4s']:
					shell('./cpumap-pping/src/xdp_iphash_to_cpu_cmdline --del --ip ' + str(ipv4))
				for ipv6 in device['ipv6s']:
					shell('./cpumap-pping/src/xdp_iphash_to_cpu_cmdline --del --ip ' + str(ipv6))
		
		
		def addDeviceIPsToFilter(circuit, cpuNumHex):
			for device in circuit['devices']:
				for ipv4 in device['ipv4s']:
					shell('./cpumap-pping/src/xdp_iphash_to_cpu_cmdline --add --ip ' + str(ipv4) + ' --cpu ' + cpuNumHex + ' --classid ' + circuit['classid'])
				for ipv6 in device['ipv6s']:
					shell('./cpumap-pping/src/xdp_iphash_to_cpu_cmdline --add --ip ' + str(ipv6) + ' --cpu ' + cpuNumHex + ' --classid ' + circuit['classid'])
		
		
		def getAllParentNodes(data, allParentNodes):
			for node in data:
				if isinstance(node, str):
					thisParentNode = node
					if thisParentNode not in allParentNodes:
						allParentNodes.append(thisParentNode)
					if 'children' in data[node]:
						for child in data[node]['children']:
							result = getAllParentNodes(data[node]['children'][child], allParentNodes)
							allParentNodes = allParentNodes + result
			return allParentNodes	
		allParentNodes = getAllParentNodes(network, [])


		def getClassIDofParentNodes(data, classIDOfParentNodes):
			for node in data:
				if isinstance(node, str):
					thisParentNode = node
					classIDOfParentNodes[thisParentNode] = data[node]['classid']
					if 'children' in data[node]:
						for child in data[node]['children']:
							result = getClassIDofParentNodes(data[node]['children'][child], classIDOfParentNodes)
							classIDOfParentNodes.update(result)
			return classIDOfParentNodes	
		classIDOfParentNodes = getClassIDofParentNodes(network, {})
		
		
		def getAllCircuitIDs(data, allCircuitIDs):
			for node in data:
				if isinstance(node, str):
					thisParentNode = node
					if 'circuits' in data[node]:
						for circuit in data[node]['circuits']:
							if circuit['circuitID'] not in allCircuitIDs:
								allCircuitIDs.append(circuit['circuitID'])
					if 'children' in data[node]:
						for child in data[node]['children']:
							result = getAllCircuitIDs(data[node]['children'][child], allCircuitIDs)
							for entry in result:
								if entry not in allCircuitIDs:
									allCircuitIDs.append(result)
			return allCircuitIDs
		allCircuitIDs = getAllCircuitIDs(network, [])


		def getClassIDofExistingCircuitID(data, classIDofExistingCircuitID):
			for node in data:
				if isinstance(node, str):
					thisParentNode = node
					if 'circuits' in data[node]:
						for circuit in data[node]['circuits']:
							classIDofExistingCircuitID[circuit['circuitID']] = circuit['classid']
					if 'children' in data[node]:
						for child in data[node]['children']:
							result = getClassIDofExistingCircuitID(data[node]['children'][child], allCircuitIDs)
							classIDofExistingCircuitID.update(result)
			return classIDofExistingCircuitID	
		classIDofExistingCircuitID = getClassIDofExistingCircuitID(network, {})
		
		
		def getParentNodeOfCircuitID(data, parentNodeOfCircuitID, allCircuitIDs):
			for node in data:
				if isinstance(node, str):
					thisParentNode = node
					if 'circuits' in data[node]:
						for circuit in data[node]['circuits']:
							parentNodeOfCircuitID[circuit['circuitID']] = thisParentNode
					if 'children' in data[node]:
						for child in data[node]['children']:
							result = getParentNodeOfCircuitID(data[node]['children'][child], parentNodeOfCircuitID, allCircuitIDs)
							parentNodeOfCircuitID.update(result)
			return parentNodeOfCircuitID	
		parentNodeOfCircuitID = getParentNodeOfCircuitID(network, {}, allCircuitIDs)
		
		
		def getCPUnumOfParentNodes(data, cpuNumOfParentNode):
			for node in data:
				if isinstance(node, str):
					thisParentNode = node
					cpuNumOfParentNode[thisParentNode] = data[node]['cpuNum']
					if 'children' in data[node]:
						for child in data[node]['children']:
							result = getCPUnumOfParentNodes(data[node]['children'][child], cpuNumOfParentNode)
							cpuNumOfParentNode.update(result)
			return cpuNumOfParentNode	
		cpuNumOfParentNodeHex = getCPUnumOfParentNodes(network, {})
		cpuNumOfParentNodeInt = {}
		for key, value in cpuNumOfParentNodeHex.items():
			cpuNumOfParentNodeInt[key] = int(value,16) + 1
		
		
		def addCircuitHTBandQdisc(circuit, parentNodeClassID):
			minor = circuit['classid'].split(':')[1]
			for interface in [interfaceA, interfaceB]:
				if interface == interfaceA:
					rate = str(circuit['minDownload'])
					ceil = str(circuit['maxDownload'])
				else:
					rate = str(circuit['minUpload'])
					ceil = str(circuit['maxUpload'])
				command = 'tc class add dev ' + interface + ' parent ' + parentNodeClassID + ' classid ' + minor + ' htb rate ' + rate + 'Mbit ceil ' + ceil + 'Mbit'
				print(command)
				shell(command)
				command = 'tc qdisc add dev ' + interface + ' parent ' + classID + ' ' + fqOrCAKE
				print(command)
				shell(command)
		
		
		def delHTBclass(classid):
			for interface in [interfaceA, interfaceB]:
				command = 'tc class del dev ' + interface + ' classid ' + classid
				print(command)
				shell(command)
		
		
		generatedPNcounter = 1
		circuitsIDsToRemove = []
		circuitsToUpdateByID = {}
		circuitsToAddByParentNode = {}
		for circuitID, circuit in lastLoadedSubscriberCircuitsByID.items():
			# Same circuit, update parameters (bandwidth, devices)
			bandwidthChanged = False
			devicesChanged = False
			if (circuitID in newlyUpdatedSubscriberCircuitsByID) and (circuitID in lastLoadedSubscriberCircuitsByID):
				if newlyUpdatedSubscriberCircuitsByID[circuitID]['maxDownload'] != lastLoadedSubscriberCircuitsByID[circuitID]['maxDownload']:
					bandwidthChanged = True
				if newlyUpdatedSubscriberCircuitsByID[circuitID]['maxUpload'] != lastLoadedSubscriberCircuitsByID[circuitID]['maxUpload']:
					bandwidthChanged = True
				if newlyUpdatedSubscriberCircuitsByID[circuitID]['minDownload'] != lastLoadedSubscriberCircuitsByID[circuitID]['minDownload']:
					bandwidthChanged = True
				if newlyUpdatedSubscriberCircuitsByID[circuitID]['minUpload'] != lastLoadedSubscriberCircuitsByID[circuitID]['minUpload']:
					bandwidthChanged = True					
				if newlyUpdatedSubscriberCircuitsByID[circuitID]['devices'] != lastLoadedSubscriberCircuitsByID[circuitID]['devices']:
					devicesChanged = True
				if bandwidthChanged == True:
					if newlyUpdatedSubscriberCircuitsByID[circuitID]['ParentNode'] == lastLoadedSubscriberCircuitsByID[circuitID]['ParentNode']:
						parentNodeActual = circuit['ParentNode']
						if parentNodeActual == 'none':
							parentNodeActual = parentNodeOfCircuitID[circuitID]
						parentNodeClassID = classIDOfParentNodes[parentNodeActual]
						classid = classIDofExistingCircuitID[circuitID]
						minor = classid.split(':')[1]
						for interface in [interfaceA, interfaceB]:
							if interface == interfaceA:
								rate = str(newlyUpdatedSubscriberCircuitsByID[circuitID]['minDownload'])
								ceil = str(newlyUpdatedSubscriberCircuitsByID[circuitID]['maxDownload'])
							else:
								rate = str(newlyUpdatedSubscriberCircuitsByID[circuitID]['minUpload'])
								ceil = str(newlyUpdatedSubscriberCircuitsByID[circuitID]['maxUpload'])
							command = 'tc class change dev ' + interface + ' parent ' + parentNodeClassID + ' classid ' + minor + ' htb rate ' + rate + 'Mbit ceil ' + ceil + 'Mbit'
							shell(command)
					else:
						removeDeviceIPsFromFilter(lastLoadedSubscriberCircuitsByID[circuitID])
						# Delete HTB class, qdisc. Then recreat it.
						classid = classIDofExistingCircuitID[circuitID]
						circuit['classid'] = classid
						delHTBclass(classID)
						parentNodeClassID = classIDOfParentNodes[parentNodeOfCircuitID[circuitID]]
						addCircuitHTBandQdisc(circuit, parentNodeClassID)
						addDeviceIPsToFilter(newlyUpdatedSubscriberCircuitsByID[circuitID], cpuNum)
				elif devicesChanged:
					removeDeviceIPsFromFilter(lastLoadedSubscriberCircuitsByID[circuitID])
					parentNodeActual = lastLoadedSubscriberCircuitsByID[circuitID]['ParentNode']
					if parentNodeActual == 'none':
						parentNodeActual = getParentNodeOfCircuitID(network, circuitID)
					cpuNum, parentNodeClassID = getCPUnumAndClassIDOfParentNode(network, parentNodeActual)
					addDeviceIPsToFilter(newlyUpdatedSubscriberCircuitsByID[circuitID], cpuNum)
				
				newlyUpdatedSubscriberCircuitsByID[circuitID]['classid'] = lastLoadedSubscriberCircuitsByID[circuitID]['classid']
				if (bandwidthChanged) or (devicesChanged):
					circuitsToUpdateByID[circuitID] = newlyUpdatedSubscriberCircuitsByID[circuitID]
			
			
			# Removed circuit
			if (circuitID in lastLoadedSubscriberCircuitsByID) and (circuitID not in newlyUpdatedSubscriberCircuitsByID):
				circuitsIDsToRemove.append(circuitID)
				removeDeviceIPsFromFilter(lastLoadedSubscriberCircuitsByID[circuitID])
				classid = classIDofExistingCircuitID[circuitID]
				delHTBclass(classid)
		
		
		# New circuit
		for circuitID, circuit in newlyUpdatedSubscriberCircuitsByID.items():
			if (circuitID in newlyUpdatedSubscriberCircuitsByID) and (circuitID not in lastLoadedSubscriberCircuitsByID):
				if newlyUpdatedSubscriberCircuitsByID[circuitID]['ParentNode'] == 'none':
					newlyUpdatedSubscriberCircuitsByID[circuitID]['ParentNode'] = 'Generated_PN_' + str(generatedPNcounter)
					generatedPNcounter += 1
					if generatedPNcounter > len(generatedPNs):
						generatedPNcounter = 1
				cpuNumHex = cpuNumOfParentNodeHex[circuit['ParentNode']]
				cpuNumInt = cpuNumOfParentNodeInt[circuit['ParentNode']]
				parentNodeClassID = classIDOfParentNodes[circuit['ParentNode']]
				classID = parentNodeClassID.split(':')[0] + ':' + hex(lastUsedClassIDCounterByCPU[str(cpuNumInt)])
				lastUsedClassIDCounterByCPU[str(cpuNumInt)] = lastUsedClassIDCounterByCPU[str(cpuNumInt)] + 1
				circuit['classid'] = classID
				# Add HTB class, qdisc
				addCircuitHTBandQdisc(circuit, parentNodeClassID)
				addDeviceIPsToFilter(circuit, cpuNumHex)
				if circuit['ParentNode'] in circuitsToAddByParentNode:
					temp = circuitsToAddByParentNode[circuit['ParentNode']]
					temp.append(circuit)
					circuitsToAddByParentNode[circuit['ParentNode']] = temp
				else:
					temp = []
					temp.append(circuit)
					circuitsToAddByParentNode[circuit['ParentNode']] = temp
		
		
		# Update network structure reflecting new circuits, removals, and changes
		itemsToChange = (circuitsIDsToRemove, circuitsToUpdateByID,	circuitsToAddByParentNode)
		def updateNetworkStructure(data, depth, itemsToChange):
			circuitsIDsToRemove, circuitsToUpdateByID,	circuitsToAddByParentNode = itemsToChange
			for node in data:
				if isinstance(node, str):
					thisParentNode = node
					if 'circuits' in data[node]:
						for circuit in data[node]['circuits']:
							if circuit['circuitID'] in circuitsToUpdateByID:
								circuit = circuitsToUpdateByID[circuit['circuitID']]
								print('updated')
							if circuit['circuitID'] in circuitsIDsToRemove:
								data[node]['circuits'].remove(circuit)
					if thisParentNode in circuitsToAddByParentNode:
						if 'circuits' in data[node]:
							temp = data[node]['circuits']
							for circuit in circuitsToAddByParentNode[thisParentNode]:
								temp.append(circuit)
							data[node]['circuits'] = temp
						else:
							temp = []
							for circuit in circuitsToAddByParentNode[thisParentNode]:
								temp.append(circuit)
							data[node]['circuits'] = temp
					if 'children' in data[node]:
						for child in data[node]['children']:
							result = updateNetworkStructure(data[node]['children'][child], depth+1, itemsToChange)
							data[node]['children'][child] = result
			return data	
		network = updateNetworkStructure(network, 0 , itemsToChange)
		
		
		# Record start time of actual filter reload
		reloadStartTime = datetime.now()
		
		
		# Record end time of all reload commands
		reloadEndTime = datetime.now()
		
		
		queuingStructure = {}
		queuingStructure['Network'] = network
		queuingStructure['lastUsedClassIDCounterByCPU'] = lastUsedClassIDCounterByCPU
		queuingStructure['generatedPNs'] = generatedPNs
		# Save queuingStructure
		with open('queuingStructure.json', 'w') as infile:
			json.dump(queuingStructure, infile, indent=4)		
		
		
		# copy ShapedDevices.csv and save as ShapedDevices.lastLoaded.csv and lastGoodConfig.csv
		shutil.copyfile('ShapedDevices.csv', 'ShapedDevices.lastLoaded.csv')
		shutil.copyfile('ShapedDevices.csv', 'lastGoodConfig.csv')
		
		
		# Save for stats
		with open('statsByCircuit.json', 'w') as f:
			f.write(json.dumps(subscriberCircuits, indent=4))
		with open('statsByParentNode.json', 'w') as f:
			f.write(json.dumps(parentNodes, indent=4))
		
		
		# Report reload time
		reloadTimeSeconds = ((reloadEndTime - reloadStartTime).seconds) + (((reloadEndTime - reloadStartTime).microseconds) / 1000000)
		print("Queue and IP filter partial reload completed in " + "{:g}".format(round(reloadTimeSeconds,1)) + " seconds")
		
		
		# Done
		print("refreshShapersUpdateOnly completed on " + datetime.now().strftime("%d/%m/%Y %H:%M:%S"))

if __name__ == '__main__':
	parser = argparse.ArgumentParser()
	parser.add_argument(
		'-d', '--debug',
		help="Print lots of debugging statements",
		action="store_const", dest="loglevel", const=logging.DEBUG,
		default=logging.WARNING,
	)
	parser.add_argument(
		'-v', '--verbose',
		help="Be verbose",
		action="store_const", dest="loglevel", const=logging.INFO,
	)
	parser.add_argument(
		'--updateonly',
		help="Only update to reflect changes in ShapedDevices.csv (partial reload)",
		action=argparse.BooleanOptionalAction,
	)
	parser.add_argument(
		'--validate',
		help="Just validate network.json and ShapedDevices.csv",
		action=argparse.BooleanOptionalAction,
	)
	parser.add_argument(
		'--clearrules',
		help="Clear ip filters, qdiscs, and xdp setup if any",
		action=argparse.BooleanOptionalAction,
	)
	args = parser.parse_args()    
	logging.basicConfig(level=args.loglevel)
	
	if args.validate:
		status = validateNetworkAndDevices()
	elif args.clearrules:
		tearDown(interfaceA, interfaceB)
	elif args.updateonly:
		refreshShapersUpdateOnly()
	else:
		# Refresh and/or set up queues
		refreshShapers()
