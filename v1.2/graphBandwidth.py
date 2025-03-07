import subprocess
import json
import subprocess
from datetime import datetime
from pathlib import Path

from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS

from ispConfig import interfaceA, interfaceB, influxDBBucket, influxDBOrg, influxDBtoken, influxDBurl, fqOrCAKE


def getInterfaceStats(interface):
	command = 'tc -j -s qdisc show dev ' + interface
	jsonAr = json.loads(subprocess.run(command.split(' '), stdout=subprocess.PIPE).stdout.decode('utf-8'))
	jsonDict = {}
	for element in filter(lambda e: 'parent' in e, jsonAr):
		flowID = ':'.join(map(lambda p: f'0x{p}', element['parent'].split(':')[0:2]))
		jsonDict[flowID] = element
	del jsonAr
	return jsonDict


def chunk_list(l, n):
	for i in range(0, len(l), n):
		yield l[i:i + n]

def getsubscriberCircuitstats(subscriberCircuits, tinsStats):
	interfaces = [interfaceA, interfaceB]
	ifaceStats = list(map(getInterfaceStats, interfaces))
	
	for circuit in subscriberCircuits:
		if 'stats' not in circuit:
			circuit['stats'] = {}
		if 'currentQuery' in circuit['stats']:
			circuit['stats']['priorQuery'] = circuit['stats']['currentQuery']
			circuit['stats']['currentQuery'] = {}
			circuit['stats']['sinceLastQuery'] = {}
		else:
			#circuit['stats']['priorQuery'] = {}
			#circuit['stats']['priorQuery']['time'] = datetime.now().isoformat()
			circuit['stats']['currentQuery'] = {}
			circuit['stats']['sinceLastQuery'] = {}

	#for entry in tinsStats:
	if 'currentQuery' in tinsStats:
		tinsStats['priorQuery'] = tinsStats['currentQuery']
		tinsStats['currentQuery'] = {}
		tinsStats['sinceLastQuery'] = {}
	else:
		tinsStats['currentQuery'] = {}
		tinsStats['sinceLastQuery'] = {}
	
	tinsStats['currentQuery'] = {	'Bulk': {'Download': {'sent_packets': 0.0, 'drops': 0.0}, 'Upload': {'sent_packets': 0.0, 'drops': 0.0}},
									'BestEffort': {'Download': {'sent_packets': 0.0, 'drops': 0.0}, 'Upload': {'sent_packets': 0.0, 'drops': 0.0}},
									'Video': {'Download': {'sent_packets': 0.0, 'drops': 0.0}, 'Upload': {'sent_packets': 0.0, 'drops': 0.0}},
									'Voice': {'Download': {'sent_packets': 0.0, 'drops': 0.0}, 'Upload': {'sent_packets': 0.0, 'drops': 0.0}},
								}
	tinsStats['sinceLastQuery'] = {	'Bulk': {'Download': {'sent_packets': 0.0, 'drops': 0.0}, 'Upload': {'sent_packets': 0.0, 'drops': 0.0}},
									'BestEffort': {'Download': {'sent_packets': 0.0, 'drops': 0.0}, 'Upload': {'sent_packets': 0.0, 'drops': 0.0}},
									'Video': {'Download': {'sent_packets': 0.0, 'drops': 0.0}, 'Upload': {'sent_packets': 0.0, 'drops': 0.0}},
									'Voice': {'Download': {'sent_packets': 0.0, 'drops': 0.0}, 'Upload': {'sent_packets': 0.0, 'drops': 0.0}},
								}
	
	for circuit in subscriberCircuits:
		for (interface, stats, dirSuffix) in zip(interfaces, ifaceStats, ['Download', 'Upload']):

			element = stats[circuit['qdisc']] if circuit['qdisc'] in stats else False

			if element:
				bytesSent = float(element['bytes'])
				drops = float(element['drops'])
				packets = float(element['packets'])
				if (element['drops'] > 0) and (element['packets'] > 0):
					overloadFactor = float(round(element['drops']/element['packets'],3))
				else:
					overloadFactor = 0.0
				
				if 'cake diffserv4' in fqOrCAKE:
					tinCounter = 1
					for tin in element['tins']:
						sent_packets = float(tin['sent_packets'])
						ack_drops = float(tin['ack_drops'])
						ecn_mark = float(tin['ecn_mark'])
						tinDrops = float(tin['drops'])
						trueDrops = ecn_mark + tinDrops - ack_drops
						if tinCounter == 1:
							tinsStats['currentQuery']['Bulk'][dirSuffix]['sent_packets'] += sent_packets
							tinsStats['currentQuery']['Bulk'][dirSuffix]['drops'] += trueDrops
						elif tinCounter == 2:
							tinsStats['currentQuery']['BestEffort'][dirSuffix]['sent_packets'] += sent_packets
							tinsStats['currentQuery']['BestEffort'][dirSuffix]['drops'] += trueDrops
						elif tinCounter == 3:
							tinsStats['currentQuery']['Video'][dirSuffix]['sent_packets'] += sent_packets
							tinsStats['currentQuery']['Video'][dirSuffix]['drops'] += trueDrops
						elif tinCounter == 4:
							tinsStats['currentQuery']['Voice'][dirSuffix]['sent_packets'] += sent_packets
							tinsStats['currentQuery']['Voice'][dirSuffix]['drops'] += trueDrops
						tinCounter += 1

				circuit['stats']['currentQuery']['bytesSent' + dirSuffix] = bytesSent
				circuit['stats']['currentQuery']['packetDrops' + dirSuffix] = drops
				circuit['stats']['currentQuery']['packetsSent' + dirSuffix] = packets
				circuit['stats']['currentQuery']['overloadFactor' + dirSuffix] = overloadFactor
				
				#if 'cake diffserv4' in fqOrCAKE:
				#	circuit['stats']['currentQuery']['tins'] = theseTins

		circuit['stats']['currentQuery']['time'] = datetime.now().isoformat()
		
	allPacketsDownload = 0.0
	allPacketsUpload = 0.0
	for circuit in subscriberCircuits:
		circuit['stats']['sinceLastQuery']['bitsDownload'] = circuit['stats']['sinceLastQuery']['bitsUpload'] = 0.0
		circuit['stats']['sinceLastQuery']['bytesSentDownload'] = circuit['stats']['sinceLastQuery']['bytesSentUpload'] = 0.0
		circuit['stats']['sinceLastQuery']['packetDropsDownload'] = circuit['stats']['sinceLastQuery']['packetDropsUpload'] = 0.0
		circuit['stats']['sinceLastQuery']['packetsSentDownload'] = circuit['stats']['sinceLastQuery']['packetsSentUpload'] = 0.0
		
		try:
			circuit['stats']['sinceLastQuery']['bytesSentDownload'] = circuit['stats']['currentQuery']['bytesSentDownload'] - circuit['stats']['priorQuery']['bytesSentDownload']
			circuit['stats']['sinceLastQuery']['bytesSentUpload'] = circuit['stats']['currentQuery']['bytesSentUpload'] - circuit['stats']['priorQuery']['bytesSentUpload']
		except:
			circuit['stats']['sinceLastQuery']['bytesSentDownload'] = 0.0
			circuit['stats']['sinceLastQuery']['bytesSentUpload'] = 0.0
		try:
			circuit['stats']['sinceLastQuery']['packetDropsDownload'] = circuit['stats']['currentQuery']['packetDropsDownload'] - circuit['stats']['priorQuery']['packetDropsDownload']
			circuit['stats']['sinceLastQuery']['packetDropsUpload'] = circuit['stats']['currentQuery']['packetDropsUpload'] - circuit['stats']['priorQuery']['packetDropsUpload']
		except:
			circuit['stats']['sinceLastQuery']['packetDropsDownload'] = 0.0
			circuit['stats']['sinceLastQuery']['packetDropsUpload'] = 0.0
		try:
			circuit['stats']['sinceLastQuery']['packetsSentDownload'] = circuit['stats']['currentQuery']['packetsSentDownload'] - circuit['stats']['priorQuery']['packetsSentDownload']
			circuit['stats']['sinceLastQuery']['packetsSentUpload'] = circuit['stats']['currentQuery']['packetsSentUpload'] - circuit['stats']['priorQuery']['packetsSentUpload']
		except:
			circuit['stats']['sinceLastQuery']['packetsSentDownload'] = 0.0
			circuit['stats']['sinceLastQuery']['packetsSentUpload'] = 0.0
		
		allPacketsDownload += circuit['stats']['sinceLastQuery']['packetsSentDownload']
		allPacketsUpload += circuit['stats']['sinceLastQuery']['packetsSentUpload']
		
		if 'priorQuery' in circuit['stats']:
			if 'time' in circuit['stats']['priorQuery']:
				currentQueryTime = datetime.fromisoformat(circuit['stats']['currentQuery']['time'])
				priorQueryTime = datetime.fromisoformat(circuit['stats']['priorQuery']['time'])
				deltaSeconds = (currentQueryTime - priorQueryTime).total_seconds()
				circuit['stats']['sinceLastQuery']['bitsDownload'] = round(
					((circuit['stats']['sinceLastQuery']['bytesSentDownload'] * 8) / deltaSeconds)) if deltaSeconds > 0 else 0
				circuit['stats']['sinceLastQuery']['bitsUpload'] = round(
					((circuit['stats']['sinceLastQuery']['bytesSentUpload'] * 8) / deltaSeconds)) if deltaSeconds > 0 else 0
		else:
			circuit['stats']['sinceLastQuery']['bitsDownload'] = (circuit['stats']['sinceLastQuery']['bytesSentDownload'] * 8)
			circuit['stats']['sinceLastQuery']['bitsUpload'] = (circuit['stats']['sinceLastQuery']['bytesSentUpload'] * 8)
	
	tinsStats['sinceLastQuery']['Bulk']['Download']['dropPercentage'] = tinsStats['sinceLastQuery']['Bulk']['Upload']['dropPercentage'] = 0.0
	tinsStats['sinceLastQuery']['BestEffort']['Download']['dropPercentage'] = tinsStats['sinceLastQuery']['BestEffort']['Upload']['dropPercentage'] = 0.0
	tinsStats['sinceLastQuery']['Video']['Download']['dropPercentage'] = tinsStats['sinceLastQuery']['Video']['Upload']['dropPercentage'] = 0.0
	tinsStats['sinceLastQuery']['Voice']['Download']['dropPercentage'] = tinsStats['sinceLastQuery']['Voice']['Upload']['dropPercentage'] = 0.0
	
	tinsStats['sinceLastQuery']['Bulk']['Download']['percentage'] = tinsStats['sinceLastQuery']['Bulk']['Upload']['percentage'] = 0.0
	tinsStats['sinceLastQuery']['BestEffort']['Download']['percentage'] = tinsStats['sinceLastQuery']['BestEffort']['Upload']['percentage'] = 0.0
	tinsStats['sinceLastQuery']['Video']['Download']['percentage'] = tinsStats['sinceLastQuery']['Video']['Upload']['percentage'] = 0.0
	tinsStats['sinceLastQuery']['Voice']['Download']['percentage'] = tinsStats['sinceLastQuery']['Voice']['Upload']['percentage'] = 0.0
	
	try:
		tinsStats['sinceLastQuery']['Bulk']['Download']['sent_packets'] = tinsStats['currentQuery']['Bulk']['Download']['sent_packets'] - tinsStats['priorQuery']['Bulk']['Download']['sent_packets']
		tinsStats['sinceLastQuery']['BestEffort']['Download']['sent_packets'] = tinsStats['currentQuery']['BestEffort']['Download']['sent_packets'] - tinsStats['priorQuery']['BestEffort']['Download']['sent_packets']
		tinsStats['sinceLastQuery']['Video']['Download']['sent_packets'] = tinsStats['currentQuery']['Video']['Download']['sent_packets'] - tinsStats['priorQuery']['Video']['Download']['sent_packets']
		tinsStats['sinceLastQuery']['Voice']['Download']['sent_packets'] = tinsStats['currentQuery']['Voice']['Download']['sent_packets'] - tinsStats['priorQuery']['Voice']['Download']['sent_packets']
		tinsStats['sinceLastQuery']['Bulk']['Upload']['sent_packets'] = tinsStats['currentQuery']['Bulk']['Upload']['sent_packets'] - tinsStats['priorQuery']['Bulk']['Upload']['sent_packets']
		tinsStats['sinceLastQuery']['BestEffort']['Upload']['sent_packets'] = tinsStats['currentQuery']['BestEffort']['Upload']['sent_packets'] - tinsStats['priorQuery']['BestEffort']['Upload']['sent_packets']
		tinsStats['sinceLastQuery']['Video']['Upload']['sent_packets'] = tinsStats['currentQuery']['Video']['Upload']['sent_packets'] - tinsStats['priorQuery']['Video']['Upload']['sent_packets']
		tinsStats['sinceLastQuery']['Voice']['Upload']['sent_packets'] = tinsStats['currentQuery']['Voice']['Upload']['sent_packets'] - tinsStats['priorQuery']['Voice']['Upload']['sent_packets']
	except:
		tinsStats['sinceLastQuery']['Bulk']['Download']['sent_packets'] = tinsStats['sinceLastQuery']['BestEffort']['Download']['sent_packets'] = 0.0
		tinsStats['sinceLastQuery']['Video']['Download']['sent_packets'] = tinsStats['sinceLastQuery']['Voice']['Download']['sent_packets'] = 0.0
		tinsStats['sinceLastQuery']['Bulk']['Upload']['sent_packets'] = tinsStats['sinceLastQuery']['BestEffort']['Upload']['sent_packets'] = 0.0
		tinsStats['sinceLastQuery']['Video']['Upload']['sent_packets'] = tinsStats['sinceLastQuery']['Voice']['Upload']['sent_packets'] = 0.0

	try:
		tinsStats['sinceLastQuery']['Bulk']['Download']['drops'] = tinsStats['currentQuery']['Bulk']['Download']['drops'] - tinsStats['priorQuery']['Bulk']['Download']['drops']
		tinsStats['sinceLastQuery']['BestEffort']['Download']['drops'] = tinsStats['currentQuery']['BestEffort']['Download']['drops'] - tinsStats['priorQuery']['BestEffort']['Download']['drops']
		tinsStats['sinceLastQuery']['Video']['Download']['drops'] = tinsStats['currentQuery']['Video']['Download']['drops'] - tinsStats['priorQuery']['Video']['Download']['drops']
		tinsStats['sinceLastQuery']['Voice']['Download']['drops'] = tinsStats['currentQuery']['Voice']['Download']['drops'] - tinsStats['priorQuery']['Voice']['Download']['drops']
		tinsStats['sinceLastQuery']['Bulk']['Upload']['drops'] = tinsStats['currentQuery']['Bulk']['Upload']['drops'] - tinsStats['priorQuery']['Bulk']['Upload']['drops']
		tinsStats['sinceLastQuery']['BestEffort']['Upload']['drops'] = tinsStats['currentQuery']['BestEffort']['Upload']['drops'] - tinsStats['priorQuery']['BestEffort']['Upload']['drops']
		tinsStats['sinceLastQuery']['Video']['Upload']['drops'] = tinsStats['currentQuery']['Video']['Upload']['drops'] - tinsStats['priorQuery']['Video']['Upload']['drops']
		tinsStats['sinceLastQuery']['Voice']['Upload']['drops'] = tinsStats['currentQuery']['Voice']['Upload']['drops'] - tinsStats['priorQuery']['Voice']['Upload']['drops']
	except:
		tinsStats['sinceLastQuery']['Bulk']['Download']['drops'] = tinsStats['sinceLastQuery']['BestEffort']['Download']['drops'] = 0.0
		tinsStats['sinceLastQuery']['Video']['Download']['drops'] = tinsStats['sinceLastQuery']['Voice']['Download']['drops'] = 0.0
		tinsStats['sinceLastQuery']['Bulk']['Upload']['drops'] = tinsStats['sinceLastQuery']['BestEffort']['Upload']['drops'] = 0.0
		tinsStats['sinceLastQuery']['Video']['Upload']['drops'] = tinsStats['sinceLastQuery']['Voice']['Upload']['drops'] = 0.0

	try:
		dlPerc = tinsStats['sinceLastQuery']['Bulk']['Download']['drops'] / tinsStats['sinceLastQuery']['Bulk']['Download']['sent_packets']
		ulPerc = tinsStats['sinceLastQuery']['Bulk']['Upload']['drops'] / tinsStats['sinceLastQuery']['Bulk']['Upload']['sent_packets']
		tinsStats['sinceLastQuery']['Bulk']['Download']['dropPercentage'] = max(round(dlPerc * 100.0, 3),0.0)
		tinsStats['sinceLastQuery']['Bulk']['Upload']['dropPercentage'] = max(round(ulPerc * 100.0, 3),0.0)
		
		dlPerc = tinsStats['sinceLastQuery']['BestEffort']['Download']['drops'] / tinsStats['sinceLastQuery']['BestEffort']['Download']['sent_packets']
		ulPerc = tinsStats['sinceLastQuery']['BestEffort']['Upload']['drops'] / tinsStats['sinceLastQuery']['BestEffort']['Upload']['sent_packets']
		tinsStats['sinceLastQuery']['BestEffort']['Download']['dropPercentage'] = max(round(dlPerc * 100.0, 3),0.0)
		tinsStats['sinceLastQuery']['BestEffort']['Upload']['dropPercentage'] = max(round(ulPerc * 100.0, 3),0.0)
		
		dlPerc = tinsStats['sinceLastQuery']['Video']['Download']['drops'] / tinsStats['sinceLastQuery']['Video']['Download']['sent_packets']
		ulPerc = tinsStats['sinceLastQuery']['Video']['Upload']['drops'] / tinsStats['sinceLastQuery']['Video']['Upload']['sent_packets']
		tinsStats['sinceLastQuery']['Video']['Download']['dropPercentage'] = max(round(dlPerc * 100.0, 3),0.0)
		tinsStats['sinceLastQuery']['Video']['Upload']['dropPercentage'] = max(round(ulPerc * 100.0, 3),0.0)
		
		dlPerc = tinsStats['sinceLastQuery']['Voice']['Download']['drops'] / tinsStats['sinceLastQuery']['Voice']['Download']['sent_packets']
		ulPerc = tinsStats['sinceLastQuery']['Voice']['Upload']['drops'] / tinsStats['sinceLastQuery']['Voice']['Upload']['sent_packets']
		tinsStats['sinceLastQuery']['Voice']['Download']['dropPercentage'] = max(round(dlPerc * 100.0, 3),0.0)
		tinsStats['sinceLastQuery']['Voice']['Upload']['dropPercentage'] = max(round(ulPerc * 100.0, 3),0.0)
	except:
		tinsStats['sinceLastQuery']['Bulk']['Download']['dropPercentage'] = 0.0
		tinsStats['sinceLastQuery']['Bulk']['Upload']['dropPercentage'] = 0.0
		tinsStats['sinceLastQuery']['BestEffort']['Download']['dropPercentage'] = 0.0
		tinsStats['sinceLastQuery']['BestEffort']['Upload']['dropPercentage'] = 0.0
		tinsStats['sinceLastQuery']['Video']['Download']['dropPercentage'] = 0.0
		tinsStats['sinceLastQuery']['Video']['Upload']['dropPercentage'] = 0.0
		tinsStats['sinceLastQuery']['Voice']['Download']['dropPercentage'] = 0.0
		tinsStats['sinceLastQuery']['Voice']['Upload']['dropPercentage'] = 0.0
		
	try:
		tinsStats['sinceLastQuery']['Bulk']['Download']['percentage'] = min(round((tinsStats['sinceLastQuery']['Bulk']['Download']['sent_packets']/allPacketsUpload)*100.0, 3),100.0)
		tinsStats['sinceLastQuery']['Bulk']['Upload']['percentage'] = min(round((tinsStats['sinceLastQuery']['Bulk']['Upload']['sent_packets']/allPacketsUpload)*100.0, 3),100.0)
		tinsStats['sinceLastQuery']['BestEffort']['Download']['percentage'] = min(round((tinsStats['sinceLastQuery']['BestEffort']['Download']['sent_packets']/allPacketsDownload)*100.0, 3),100.0)
		tinsStats['sinceLastQuery']['BestEffort']['Upload']['percentage'] = min(round((tinsStats['sinceLastQuery']['BestEffort']['Upload']['sent_packets']/allPacketsUpload)*100.0, 3),100.0)
		tinsStats['sinceLastQuery']['Video']['Download']['percentage'] = min(round((tinsStats['sinceLastQuery']['Video']['Download']['sent_packets']/allPacketsDownload)*100.0, 3),100.0)
		tinsStats['sinceLastQuery']['Video']['Upload']['percentage'] = min(round((tinsStats['sinceLastQuery']['Video']['Upload']['sent_packets']/allPacketsUpload)*100.0, 3),100.0)
		tinsStats['sinceLastQuery']['Voice']['Download']['percentage'] = min(round((tinsStats['sinceLastQuery']['Voice']['Download']['sent_packets']/allPacketsDownload)*100.0, 3),100.0)
		tinsStats['sinceLastQuery']['Voice']['Upload']['percentage'] = min(round((tinsStats['sinceLastQuery']['Voice']['Upload']['sent_packets']/allPacketsUpload)*100.0, 3),100.0)
	except:
		tinsStats['sinceLastQuery']['Bulk']['Download']['percentage'] = tinsStats['sinceLastQuery']['Bulk']['Upload']['percentage'] = 0.0
		tinsStats['sinceLastQuery']['BestEffort']['Download']['percentage'] = tinsStats['sinceLastQuery']['BestEffort']['Upload']['percentage'] = 0.0
		tinsStats['sinceLastQuery']['Video']['Download']['percentage'] = tinsStats['sinceLastQuery']['Video']['Upload']['percentage'] = 0.0
		tinsStats['sinceLastQuery']['Voice']['Download']['percentage'] = tinsStats['sinceLastQuery']['Voice']['Upload']['percentage'] = 0.0
	
	return subscriberCircuits, tinsStats


def getParentNodeStats(parentNodes, subscriberCircuits):
	for parentNode in parentNodes:
		thisNodeDropsDownload = 0
		thisNodeDropsUpload = 0
		thisNodeDropsTotal = 0
		thisNodeBitsDownload = 0
		thisNodeBitsUpload = 0
		packetsSentDownloadAggregate = 0.0
		packetsSentUploadAggregate = 0.0
		packetsSentTotalAggregate = 0.0
		circuitsMatched = 0
		thisParentNodeStats = {'sinceLastQuery': {}}
		for circuit in subscriberCircuits:
			if circuit['ParentNode'] == parentNode['parentNodeName']:
				thisNodeBitsDownload += circuit['stats']['sinceLastQuery']['bitsDownload']
				thisNodeBitsUpload += circuit['stats']['sinceLastQuery']['bitsUpload']
				#thisNodeDropsDownload += circuit['packetDropsDownloadSinceLastQuery']
				#thisNodeDropsUpload += circuit['packetDropsUploadSinceLastQuery']
				thisNodeDropsTotal += (circuit['stats']['sinceLastQuery']['packetDropsDownload'] + circuit['stats']['sinceLastQuery']['packetDropsUpload'])
				packetsSentDownloadAggregate += circuit['stats']['sinceLastQuery']['packetsSentDownload']
				packetsSentUploadAggregate += circuit['stats']['sinceLastQuery']['packetsSentUpload']
				packetsSentTotalAggregate += (circuit['stats']['sinceLastQuery']['packetsSentDownload'] + circuit['stats']['sinceLastQuery']['packetsSentUpload'])
				circuitsMatched += 1
		if (packetsSentDownloadAggregate > 0) and (packetsSentUploadAggregate > 0):
			#overloadFactorDownloadSinceLastQuery = float(round((thisNodeDropsDownload/packetsSentDownloadAggregate)*100.0, 3))
			#overloadFactorUploadSinceLastQuery = float(round((thisNodeDropsUpload/packetsSentUploadAggregate)*100.0, 3))
			overloadFactorTotalSinceLastQuery = float(round((thisNodeDropsTotal/packetsSentTotalAggregate)*100.0, 1))
		else:
			#overloadFactorDownloadSinceLastQuery = 0.0
			#overloadFactorUploadSinceLastQuery = 0.0
			overloadFactorTotalSinceLastQuery = 0.0
		
		thisParentNodeStats['sinceLastQuery']['bitsDownload'] = thisNodeBitsDownload
		thisParentNodeStats['sinceLastQuery']['bitsUpload'] = thisNodeBitsUpload
		thisParentNodeStats['sinceLastQuery']['packetDropsTotal'] = thisNodeDropsTotal
		thisParentNodeStats['sinceLastQuery']['overloadFactorTotal'] = overloadFactorTotalSinceLastQuery
		parentNode['stats'] = thisParentNodeStats
		
	return parentNodes


def getParentNodeDict(data, depth, parentNodeNameDict):
	if parentNodeNameDict == None:
		parentNodeNameDict = {}

	for elem in data:
		if 'children' in data[elem]:
			for child in data[elem]['children']:
				parentNodeNameDict[child] = elem
			tempDict = getParentNodeDict(data[elem]['children'], depth + 1, parentNodeNameDict)
			parentNodeNameDict = dict(parentNodeNameDict, **tempDict)
	return parentNodeNameDict


def parentNodeNameDictPull():
	# Load network heirarchy
	with open('network.json', 'r') as j:
		network = json.loads(j.read())
	parentNodeNameDict = getParentNodeDict(network, 0, None)
	return parentNodeNameDict

def refreshBandwidthGraphs():
	startTime = datetime.now()
	with open('statsByParentNode.json', 'r') as j:
		parentNodes = json.loads(j.read())

	with open('statsByCircuit.json', 'r') as j:
		subscriberCircuits = json.loads(j.read())
							
	fileLoc = Path("tinsStats.json")
	if fileLoc.is_file():
		with open(fileLoc, 'r') as j:
			tinsStats = json.loads(j.read())
	else:
		tinsStats =	{}					
							
	fileLoc = Path("longTermStats.json")
	if fileLoc.is_file():
		with open(fileLoc, 'r') as j:
			longTermStats = json.loads(j.read())
		droppedPacketsAllTime = longTermStats['droppedPacketsTotal']
	else:
		longTermStats = {}
		longTermStats['droppedPacketsTotal'] = 0.0
		droppedPacketsAllTime = 0.0

	parentNodeNameDict = parentNodeNameDictPull()

	print("Retrieving circuit statistics")
	subscriberCircuits, tinsStats = getsubscriberCircuitstats(subscriberCircuits, tinsStats)
	print("Computing parent node statistics")
	parentNodes = getParentNodeStats(parentNodes, subscriberCircuits)
	print("Writing data to InfluxDB")
	client = InfluxDBClient(
		url=influxDBurl,
		token=influxDBtoken,
		org=influxDBOrg
	)
	write_api = client.write_api(write_options=SYNCHRONOUS)

	chunkedsubscriberCircuits = list(chunk_list(subscriberCircuits, 200))

	queriesToSendCount = 0
	for chunk in chunkedsubscriberCircuits:
		queriesToSend = []
		for circuit in chunk:
			bitsDownload = float(circuit['stats']['sinceLastQuery']['bitsDownload'])
			bitsUpload = float(circuit['stats']['sinceLastQuery']['bitsUpload'])
			if (bitsDownload > 0) and (bitsUpload > 0):
				percentUtilizationDownload = round((bitsDownload / round(circuit['downloadMax'] * 1000000))*100.0, 1)
				percentUtilizationUpload = round((bitsUpload / round(circuit['uploadMax'] * 1000000))*100.0, 1)
				p = Point('Bandwidth').tag("Circuit", circuit['circuitName']).tag("ParentNode", circuit['ParentNode']).tag("Type", "Circuit").field("Download", bitsDownload).field("Upload", bitsUpload)
				queriesToSend.append(p)
				p = Point('Utilization').tag("Circuit", circuit['circuitName']).tag("ParentNode", circuit['ParentNode']).tag("Type", "Circuit").field("Download", percentUtilizationDownload).field("Upload", percentUtilizationUpload)
				queriesToSend.append(p)

		write_api.write(bucket=influxDBBucket, record=queriesToSend)
		# print("Added " + str(len(queriesToSend)) + " points to InfluxDB.")
		queriesToSendCount += len(queriesToSend)

	queriesToSend = []
	for parentNode in parentNodes:
		bitsDownload = float(parentNode['stats']['sinceLastQuery']['bitsDownload'])
		bitsUpload = float(parentNode['stats']['sinceLastQuery']['bitsUpload'])
		dropsTotal = float(parentNode['stats']['sinceLastQuery']['packetDropsTotal'])
		overloadFactor = float(parentNode['stats']['sinceLastQuery']['overloadFactorTotal'])
		droppedPacketsAllTime += dropsTotal
		if (bitsDownload > 0) and (bitsUpload > 0):
			percentUtilizationDownload = round((bitsDownload / round(parentNode['downloadMax'] * 1000000))*100.0, 1)
			percentUtilizationUpload = round((bitsUpload / round(parentNode['uploadMax'] * 1000000))*100.0, 1)
			p = Point('Bandwidth').tag("Device", parentNode['parentNodeName']).tag("ParentNode", parentNode['parentNodeName']).tag("Type", "Parent Node").field("Download", bitsDownload).field("Upload", bitsUpload)
			queriesToSend.append(p)
			p = Point('Utilization').tag("Device", parentNode['parentNodeName']).tag("ParentNode", parentNode['parentNodeName']).tag("Type", "Parent Node").field("Download", percentUtilizationDownload).field("Upload", percentUtilizationUpload)
			queriesToSend.append(p)
			p = Point('Overload').tag("Device", parentNode['parentNodeName']).tag("ParentNode", parentNode['parentNodeName']).tag("Type", "Parent Node").field("Overload", overloadFactor)
			queriesToSend.append(p)

	write_api.write(bucket=influxDBBucket, record=queriesToSend)
	# print("Added " + str(len(queriesToSend)) + " points to InfluxDB.")
	queriesToSendCount += len(queriesToSend)
	
	if 'cake diffserv4' in fqOrCAKE:
		queriesToSend = []
		listOfTins = ['Bulk', 'BestEffort', 'Video', 'Voice']
		for tin in listOfTins:
			p = Point('Tin Drop Percentage').tag("Type", "Tin").tag("Tin", tin).field("Download", tinsStats['sinceLastQuery'][tin]['Download']['dropPercentage']).field("Upload", tinsStats['sinceLastQuery'][tin]['Upload']['dropPercentage'])
			queriesToSend.append(p)
			p = Point('Tins Assigned').tag("Type", "Tin").tag("Tin", tin).field("Download", tinsStats['sinceLastQuery'][tin]['Download']['percentage']).field("Upload", tinsStats['sinceLastQuery'][tin]['Upload']['percentage'])
			queriesToSend.append(p)

		write_api.write(bucket=influxDBBucket, record=queriesToSend)
		# print("Added " + str(len(queriesToSend)) + " points to InfluxDB.")
		queriesToSendCount += len(queriesToSend)
	
	print("Added " + str(queriesToSendCount) + " points to InfluxDB.")

	client.close()
	
	with open('statsByParentNode.json', 'w') as f:
		f.write(json.dumps(parentNodes, indent=4))

	with open('statsByCircuit.json', 'w') as f:
		f.write(json.dumps(subscriberCircuits, indent=4))
	
	longTermStats['droppedPacketsTotal'] = droppedPacketsAllTime
	with open('longTermStats.json', 'w') as f:
		f.write(json.dumps(longTermStats, indent=4))
		
	with open('tinsStats.json', 'w') as f:
		f.write(json.dumps(tinsStats, indent=4))

	endTime = datetime.now()
	durationSeconds = round((endTime - startTime).total_seconds(), 2)
	print("Graphs updated within " + str(durationSeconds) + " seconds.")

if __name__ == '__main__':
	refreshBandwidthGraphs()
