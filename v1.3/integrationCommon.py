# Provides common functionality shared between
# integrations.

from typing import List, Any
from ispConfig import allowedSubnets, ignoreSubnets, generatedPNUploadMbps, generatedPNDownloadMbps
import ipaddress
import enum


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


def isIpv4Permitted(inputIP):
    # Checks whether an IP address is in Allowed Subnets.
    # If it is, check that it isn't in Ignored Subnets.
    # If it is allowed and not ignored, returns true.
    # Otherwise, returns false.
    return isInIgnoredSubnets(inputIP) == False and isInAllowedSubnets(inputIP)


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

class NodeType(enum.IntEnum):
    # Enumeration to define what type of node
    # a NetworkNode is.
    root = 1
    site = 2
    ap = 3
    client = 4
    clientWithChildren = 5
    device = 6


class NetworkNode:
    # Defines a node on a LibreQoS network graph.
    # Nodes default to being disconnected, and
    # will be mapped to the root of the overall
    # graph.

    id: str
    displayName: str
    parentIndex: int
    parentId: str
    type: NodeType
    downloadMbps: int
    uploadMbps: int
    ipv4: List
    ipv6: List
    address: str
    mac: str

    def __init__(self, id: str, displayName: str = "", parentId: str = "", type: NodeType = NodeType.site, download: int = generatedPNDownloadMbps, upload: int = generatedPNUploadMbps, ipv4: List = [], ipv6: List = [], address: str = "", mac: str = "") -> None:
        self.id = id
        self.parentIndex = 0
        self.type = type
        self.parentId = parentId
        if displayName == "":
            self.displayName = id
        else:
            self.displayName = displayName
        self.downloadMbps = download
        self.uploadMbps = upload
        self.ipv4 = ipv4
        self.ipv6 = ipv6
        self.address = address
        self.mac = mac


class NetworkGraph:
    # Defines a network as a graph topology
    # allowing any integration to build the
    # graph via a common API, emitting
    # ShapedDevices and network.json files
    # via a common interface.

    nodes: List
    ipv4ToIPv6: Any
    excludeSites: List # Copied to allow easy in-test patching
    exceptionCPEs: Any

    def __init__(self) -> None:
        from ispConfig import findIPv6usingMikrotik, excludeSites, exceptionCPEs
        self.nodes = [
            NetworkNode("FakeRoot", type=NodeType.root,
                        parentId="", displayName="Shaper Root")
        ]
        self.excludeSites = excludeSites
        self.exceptionCPEs = exceptionCPEs
        if findIPv6usingMikrotik:
            from mikrotikFindIPv6 import pullMikrotikIPv6  
            self.ipv4ToIPv6 = pullMikrotikIPv6()
        else:
            self.ipv4ToIPv6 = {}

    def addRawNode(self, node: NetworkNode) -> None:
        # Adds a NetworkNode to the graph, unchanged.
        # If a site is excluded (via excludedSites in ispConfig)
        # it won't be added
        if not node.displayName in self.excludeSites:
            if node.displayName in self.exceptionCPEs.keys():
                node.parentId = self.exceptionCPEs[node.displayName]
            self.nodes.append(node)

    def replaceRootNote(self, node: NetworkNode) -> None:
        # Replaces the automatically generated root node
        # with a new node. Useful when you have a top-level
        # node specified (e.g. "uispSite" in the UISP
        # integration)
        self.nodes[0] = node

    def addNodeAsChild(self, parent: str, node: NetworkNode) -> None:
        # Searches the existing graph for a named parent,
        # adjusts the new node's parentIndex to match the new
        # node. The parented node is then inserted.
        #
        # Exceptions are NOT applied, since we're explicitly
        # specifying the parent - we're assuming you really
        # meant it.
        if node.displayName in self.excludeSites: return
        parentIdx = 0
        for (i, node) in enumerate(self.nodes):
            if node.id == parent:
                parentIdx = i
        node.parentIndex = parentIdx
        self.nodes.append(node)

    def __reparentById(self) -> None:
        # Scans the entire node tree, searching for parents
        # by name. Entries are re-mapped to match the named
        # parents. You can use this to build a tree from a
        # blob of raw data.
        for child in self.nodes:
            if child.parentId != "":
                for (i, node) in enumerate(self.nodes):
                    if node.id == child.parentId:
                        child.parentIndex = i

    def findNodeIndexById(self, id: str) -> int:
        # Finds a single node by identity(id)
        # Return -1 if not found
        for (i, node) in enumerate(self.nodes):
            if node.id == id:
                return i
        return -1

    def findNodeIndexByName(self, name: str) -> int:
        # Finds a single node by identity(name)
        # Return -1 if not found
        for (i, node) in enumerate(self.nodes):
            if node.displayName == name:
                return i
        return -1

    def findChildIndices(self, parentIndex: int) -> List:
        # Returns the indices of all nodes with a
        # parentIndex equal to the specified parameter
        result = []
        for (i, node) in enumerate(self.nodes):
            if node.parentIndex == parentIndex:
                result.append(i)
        return result

    def __promoteClientsWithChildren(self) -> None:
        # Searches for client sites that have children,
        # and changes their node type to clientWithChildren
        for (i, node) in enumerate(self.nodes):
            if node.type == NodeType.client:
                for child in self.findChildIndices(i):
                    if self.nodes[child].type != NodeType.device:
                        node.type = NodeType.clientWithChildren

    def __clientsWithChildrenToSites(self) -> None:
        toAdd = []
        for (i, node) in enumerate(self.nodes):
            if node.type == NodeType.clientWithChildren:
                siteNode = NetworkNode(
                    id=node.id + "_gen",
                    displayName="(Generated Site) " + node.displayName,
                    type=NodeType.site
                )
                siteNode.parentIndex = node.parentIndex
                node.parentId = siteNode.id
                if node.type == NodeType.clientWithChildren:
                    node.type = NodeType.client
                for child in self.findChildIndices(i):
                    if self.nodes[child].type == NodeType.client or self.nodes[child].type == NodeType.clientWithChildren or self.nodes[child].type == NodeType.site:
                        self.nodes[child].parentId = siteNode.id
                toAdd.append(siteNode)

        for n in toAdd:
            self.addRawNode(n)

        self.__reparentById()

    def __findUnconnectedNodes(self) -> List:
        # Performs a tree-traversal and finds any nodes that
        # aren't connected to the root. This is a "sanity check",
        # and also an easy way to handle "flat" topologies and
        # ensure that the unconnected nodes are re-connected to
        # the root.
        visited = []
        next = [0]

        while len(next) > 0:
            nextTraversal = next.pop()
            visited.append(nextTraversal)
            for idx in self.findChildIndices(nextTraversal):
                if idx not in visited:
                    next.append(idx)

        result = []
        for i, n in enumerate(self.nodes):
            if i not in visited:
                result.append(i)
        return result

    def __reconnectUnconnected(self):
        # Finds any unconnected nodes and reconnects
        # them to the root
        for idx in self.__findUnconnectedNodes():
            if self.nodes[idx].type == NodeType.site:
                self.nodes[idx].parentIndex = 0
        for idx in self.__findUnconnectedNodes():
            if self.nodes[idx].type == NodeType.clientWithChildren:
                self.nodes[idx].parentIndex = 0
        for idx in self.__findUnconnectedNodes():
            if self.nodes[idx].type == NodeType.client:
                self.nodes[idx].parentIndex = 0

    def prepareTree(self) -> None:
        # Helper function that calls all the cleanup and mapping
        # functions in the right order. Unless you are doing
        # something special, you can use this instead of
        # calling the functions individually
        self.__reparentById()
        self.__promoteClientsWithChildren()
        self.__clientsWithChildrenToSites()
        self.__reconnectUnconnected()

    def doesNetworkJsonExist(self):
        # Returns true if "network.json" exists, false otherwise
        import os
        return os.path.isfile("network.json")

    def __isSite(self, index) -> bool:
        return self.nodes[index].type == NodeType.ap or self.nodes[index].type == NodeType.site or self.nodes[index].type == NodeType.clientWithChildren

    def createNetworkJson(self):
        import json
        topLevelNode = {}
        self.__visited = []  # Protection against loops - never visit twice

        for child in self.findChildIndices(0):
            if child > 0 and self.__isSite(child):
                topLevelNode[self.nodes[child].displayName] = self.__buildNetworkObject(
                    child)

        del self.__visited

        with open('network.json', 'w') as f:
            json.dump(topLevelNode, f, indent=4)

    def __buildNetworkObject(self, idx):
        # Private: used to recurse down the network tree while building
        # network.json
        self.__visited.append(idx)
        node = {
            "downloadBandwidthMbps": self.nodes[idx].downloadMbps,
            "uploadBandwidthMbps": self.nodes[idx].uploadMbps,
        }
        children = {}
        hasChildren = False
        for child in self.findChildIndices(idx):
            if child > 0 and self.__isSite(child) and child not in self.__visited:
                children[self.nodes[child].displayName] = self.__buildNetworkObject(
                    child)
                hasChildren = True
        if hasChildren:
            node["children"] = children
        return node

    def __addIpv6FromMap(self, ipv4, ipv6) -> None:
        # Scans each address in ipv4. If its present in the
        # IPv4 to Ipv6 map (currently pulled from Mikrotik devices
        # if findIPv6usingMikrotik is enabled), then matching
        # IPv6 networks are appended to the ipv6 list.
        # This is explicitly non-destructive of the existing IPv6
        # list, in case you already have some.
        for ipCidr in ipv4:
            if '/' in ipCidr: ip = ipCidr.split('/')[0]
            else: ip = ipCidr
            if ip in self.ipv4ToIPv6.keys():
                ipv6.append(self.ipv4ToIPv6[ip])

    def createShapedDevices(self):
            import csv
            from ispConfig import bandwidthOverheadFactor
        # Builds ShapedDevices.csv from the network tree.
            circuits = []
            nextId = 0
            for (i, node) in enumerate(self.nodes):
                if node.type == NodeType.client:
                    parent = self.nodes[node.parentIndex].displayName
                    if parent == "Shaper Root": parent = ""
                    circuit = {
                        "id": nextId,
                        "name": node.address,
                        "parent": parent,
                        "download": node.downloadMbps,
                        "upload": node.uploadMbps,
                        "devices": []
                    }
                    for child in self.findChildIndices(i):
                        if self.nodes[child].type == NodeType.device and (len(self.nodes[child].ipv4)+len(self.nodes[child].ipv6)>0):
                            ipv4 = self.nodes[child].ipv4
                            ipv6 = self.nodes[child].ipv6
                            self.__addIpv6FromMap(ipv4, ipv6)
                            device = {
                                "id": self.nodes[child].id,
                                "name": self.nodes[child].displayName,
                                "mac": self.nodes[child].mac,
                                "ipv4": ipv4,
                                "ipv6": ipv6,
                            }
                            circuit["devices"].append(device)
                    if len(circuit["devices"]) > 0:
                        circuits.append(circuit)
                        nextId += 1

            with open('ShapedDevices.csv', 'w', newline='') as csvfile:
                wr = csv.writer(csvfile, quoting=csv.QUOTE_ALL)
                wr.writerow(['Circuit ID', 'Circuit Name', 'Device ID', 'Device Name', 'Parent Node', 'MAC',
                             'IPv4', 'IPv6', 'Download Min', 'Upload Min', 'Download Max', 'Upload Max', 'Comment'])
                for circuit in circuits:
                    for device in circuit["devices"]:
                        #Remove brackets and quotes of list so LibreQoS.py can parse it
                        device["ipv4"] = str(device["ipv4"]).replace('[','').replace(']','').replace("'",'')
                        device["ipv6"] = str(device["ipv6"]).replace('[','').replace(']','').replace("'",'')
                        row = [
                            circuit["id"],
                            circuit["name"],
                            device["id"],
                            device["name"],
                            circuit["parent"],
                            device["mac"],
                            device["ipv4"],
                            device["ipv6"],
                            int(circuit["download"] * 0.98),
                            int(circuit["upload"] * 0.98),
                            int(circuit["download"] * bandwidthOverheadFactor),
                            int(circuit["upload"] * bandwidthOverheadFactor),
                            ""
                        ]
                        wr.writerow(row)

    def plotNetworkGraph(self, showClients=False):
        # Requires `pip install graphviz` to function.
        # You also need to install graphviz on your PC.
        # In Ubuntu, apt install graphviz will do it.
        # Plots the network graph to a PDF file, allowing
        # visual verification that the graph makes sense.
        # Could potentially be useful in a future
        # web interface.
        import importlib.util
        if (spec := importlib.util.find_spec('graphviz')) is None:
            return

        import graphviz
        dot = graphviz.Digraph(
            'network', comment="Network Graph", engine="fdp")

        for (i, node) in enumerate(self.nodes):
            if ((node.type != NodeType.client and node.type != NodeType.device) or showClients):
                color = "white"
                match node.type:
                    case NodeType.root: color = "green"
                    case NodeType.site: color = "red"
                    case NodeType.ap: color = "blue"
                    case NodeType.clientWithChildren: color = "magenta"
                    case NodeType.device: color = "white"
                    case default: color = "grey"
                dot.node("N" + str(i), node.displayName, color=color)
                children = self.findChildIndices(i)
                for child in children:
                    if child != i:
                        if (self.nodes[child].type != NodeType.client and self.nodes[child].type != NodeType.device) or showClients:
                            dot.edge("N" + str(i), "N" + str(child))

        dot.render("network.pdf")

