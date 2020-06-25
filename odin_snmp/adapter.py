import logging
import tornado
import time
import sys
from concurrent import futures
from pysnmp.hlapi import *

from tornado.ioloop import IOLoop, PeriodicCallback
from tornado.concurrent import run_on_executor
from tornado.escape import json_decode

from odin.adapters.adapter import ApiAdapter, ApiAdapterResponse, request_types, response_types
from odin.adapters.parameter_tree import ParameterTree, ParameterTreeError
from odin._version import get_versions

class SnmpAdapter(ApiAdapter):
    """SNMP packet counter adapter class for the ODIN server.

    This adapter provides ODIN clients with information about packet flow
    through a network switch - typically about data flowing from the detector
    to the compute nodes."""

    def __init__(self, **kwargs):
        """Initialize the SnmpAdapter object.

        This constructor initializes the SnmpAdapter object.
        :param kwargs: keyword arguments specifying options"""

        # Intialise superclass
        super(SnmpAdapter, self).__init__(**kwargs)

        # Parse network device name
        networkDevice = str(self.options.get('networkDevice', 'devswitch5920'))

        # Parse network device port configuration
        
        if 'ports' in self.options:
            ports = {int(key):value for (key,value) in (port.strip().split(':') for port in self.options['ports'].split(','))}
        else:
            ports = None
        
        # Parse the packet count types
        oids = tuple(self.options.get('oids', 'ifInUcastPkts, ifOutUcastPkts').strip().split(','))

        # Parse the sampling interval
        try:
            samplingInterval = float(self.options.get('samplingInterval', 1.0))
        except ValueError as parse_error:
            logging.warn('Unable to parse sampling interval from config file: {}'.format(parse_error))
            samplingInterval = 1.0


        self.snmp_requester = SnmpRequester(networkDevice, oids, ports, samplingInterval)
        
        logging.debug('SNMP Adapter loaded')

    @response_types('application/json', default='application/json')
    def get(self, path, request):
        """Handle an HTTP GET request.

        This method handles an HTTP GET request, returning a JSON response.

        :param path: URI path of request
        :param request: HTTP request object
        :return: an ApiAdapterResponse object containing the appropriate response."""

        try:
            response = self.snmp_requester.get(path)
            status_code = 200
        except ParameterTreeError as e:
            response = {'error': str(e)}
            status_code = 400

        content_type = 'application/json'

        return ApiAdapterResponse(response, content_type=content_type,
                                  status_code=status_code)

    @request_types('application/json')
    @response_types('application/json', default='application/json')
    def put(self, path, request):
        """Handle an HTTP PUT request.

        This method handles an HTTP PUT request, returning a JSON response.

        :param path: URI path of request
        :param request: HTTP request object
        :return: an ApiAdapterResponse object containing the appropriate response"""

        content_type = 'application/json'

        try:
            data = json_decode(request.body)
            self.snmp_requester.set(path, data)
            response = self.snmp_requester.get(path)
            status_code = 200
        except SnmpAdapterError as e:
            response = {'error': str(e)}
            status_code = 400
        except (TypeError, ValueError) as e:
            response = {'error': 'Failed to decode PUT request body: {}'.format(str(e))}
            status_code = 400

        logging.debug(response)

        return ApiAdapterResponse(response, content_type=content_type,
                                  status_code=status_code)

    def delete(self, path, request):
        """Handle an HTTP DELETE request.

        This method handles an HTTP DELETE request, returning a JSON response.

        :param path: URI path of request
        :param request: HTTP request object
        :return: an ApiAdapterResponse object containing the appropriate response"""

        response = 'SnmpAdapter: DELETE on path {}'.format(path)
        status_code = 200

        logging.debug(response)

        return ApiAdapterResponse(response, status_code=status_code)

    def cleanup(self):
        """Clean up adapter state at shutdown.

        This method cleans up the adapter state when called by the server at e.g. shutdown.
        It simplied calls the cleanup function of the snmp_adapter instance."""

        self.snmp_requester.cleanup()

class SnmpAdapterError(Exception):
    """A simple exception class to wrap lower-level exceptions."""
    
    pass

class SnmpRequester:
    """A class that extracts and stores information about system-level parameters.
    
    This class periodically fetches snapshots of the packet flow count data via the SNMP protocol,
    and stores the latest snaphot as an instance of the SnmpSnapshot class."""

    # Thread executor used for background tasks
    executor = futures.ThreadPoolExecutor(max_workers=1)

    def __init__(self, networkDevice, oids, ports, samplingInterval):
        """Initialise the SnmpRequester object.

        This constructor initlialises the SnmpRequester object, building a parameter tree and
        launching the periodic SNMP request loop."""

        # Save arguments
        self.networkDevice = networkDevice
        self.oids = oids
        self.ports = ports
        self.samplingInterval = max(samplingInterval, 1)

        # Initialise SNMP library classes and fetch port index table outside the time loop
        # for an optimised run
        self.start_snmp_engine()
        self.indices = []
        self.fetch_all_port_indices()

        # Define ports and initialise the snapshot
        self.define_ports()
        self.initialize_snapshot_with_zeros()

        # Build a parameter tree for the background task
        self.param_tree = ParameterTree({
            'total_packet_count': (lambda: self.snapshot.packets, None),
            'relative_packet_count': (lambda: self.snapshot.delta, None),
            'ports': self.ports,
            'interval': (lambda: self.samplingInterval, self.set_sampling_interval),
        })

        # Launch the background task
        self.background_thread_task()
          
    def get(self, path):
        """Get the parameter tree.

        This method returns the parameter tree for use by clients via the Odin-SNMP adapter.

        :param path: path to retrieve from tree"""

        return self.param_tree.get(path)

    def set(self, path, data):
        """Set parameters in the parameter tree.

        This method simply wraps underlying ParameterTree method so that an exceptions can be
        re-raised with an appropriate SnmpAdapterError.

        :param path: path of parameter tree to set values for
        :param data: dictionary of new data values to set in the parameter tree"""

        try:
            self.param_tree.set(path, data)
        except ParameterTreeError as e:
            raise SnmpAdapterError(e)

    def cleanup(self):
        """Clean up the SnmpRequester instance.

        This method stops the background tasks, allowing the adapter state to be cleaned up
        correctly."""
        
        self.background_thread_task_enable = False

    def set_sampling_interval(self, interval):
        """Set the background task interval."""
        self.samplingInterval = max(1.0, float(interval))
        if interval < self.samplingInterval:
            logging.debug("The interval must not be lower than 1 sec.")
        logging.debug("Setting background task interval to %f", self.samplingInterval)

    def start_snmp_engine(self):
        """Instantiate the runtime-expensive pysnmp.hlapi library classes outside the time loop"""
        self.snmp_engine = SnmpEngine()
        self.community_data = CommunityData('public')
        self.udp_transport_target = UdpTransportTarget((self.networkDevice, 161))
        self.context_data = ContextData()

    def fetch_all_port_indices(self):
        snmp_objs = [ObjectType(ObjectIdentity('IF-MIB', 'ifIndex'))]
        g = bulkCmd(
                self.snmp_engine,
                self.community_data,
                self.udp_transport_target,
                self.context_data,
                0, 30, # setting 0 to n makes n first OIDs/MIBs fixed in ID and value
                *snmp_objs,
                lexicographicMode=False,
        )

        while True:
            """Generate index numbers for all ports in the obtained SNMP table.
            varBind[n][0] stores the ID, and
            varBind[n][1] stores the vaule corresponding to the n-th OID"""
            try:
                errorIndication, errorStatus, errorIndex, varBinds = next(g)
                if errorIndication:
                    print(errorIndication)
                    break
                elif errorStatus:
                    print('%s at %s' % (errorStatus.prettyPrint(),
                                        errorIndex and varBinds[int(errorIndex) - 1][0] or '?'))
                    break
                else:
                    self.indices.append(int(varBinds[0][1]))
            except StopIteration:
                break

    def define_ports(self):
        if self.ports == None: # != for testing - convert to == when done
            logging.debug('No ports have been specified in the config file. The adapter will listen on all existing ports.')
            self.fetch_all_port_names()
            logging.debug('The following ports have been detected on the network device:')
        else:
            logging.debug('The SNMP adapter has been configured for the following network device ports:')
        for port in self.ports.keys():
            logging.debug('Port number: {} Port name: {}'.format(port, self.ports[port]))

    def fetch_all_port_names(self):
        snmp_objs = [ObjectType(ObjectIdentity('IF-MIB', 'ifName'))]
        g = bulkCmd(
                self.snmp_engine,
                self.community_data,
                self.udp_transport_target,
                self.context_data,
                0, 30, # setting 0 to n makes n first OIDs/MIBs fixed in ID and value
                *snmp_objs,
                lexicographicMode=False,
        )

        for index in self.indices:
            """Generate index numbers for all ports in the obtained SNMP table.
            varBind[n][0] stores the ID, and
            varBind[n][1] stores the vaule corresponding to the n-th OID"""
            try:
                errorIndication, errorStatus, errorIndex, varBinds = next(g)
                if errorIndication:
                    print(errorIndication)
                    break
                elif errorStatus:
                    print('%s at %s' % (errorStatus.prettyPrint(),
                                        errorIndex and varBinds[int(errorIndex) - 1][0] or '?'))
                    break
                else:
                    self.ports[index] = str(varBinds[0][1])
            except StopIteration:
                break

    def initialize_snapshot_with_zeros(self):
        self.collector = SnmpSnapshot(self.ports)
        self.snapshot = self.collector
        for port in self.ports.keys():
            self.collector.feed_port_data(port, 0, 0)
        self.snapshot.compute_delta(self.collector)

    def fetch_all_packet_counts(self):
        """Generate current packet counts for all ports in range"""
        snmp_objs = [ObjectType(ObjectIdentity('IF-MIB', oid)) for oid in self.oids]
        """Request values of the specified SNMP entities (OIDs), i.e. packets counts, for all ports"""
        g = bulkCmd(
                self.snmp_engine,
                self.community_data,
                self.udp_transport_target,
                self.context_data,
                0, 30, # setting 0 to n makes n first OIDs/MIBs fixed in ID and value
                *snmp_objs,
                lexicographicMode=False,
        )

        """Match pre-fetched index numbers with corresponding packet counts"""
        for index in self.indices:
            """Generate data for the next available port in the obtained SNMP table.
            varBind[n][0] stores the ID, and
            varBind[n][1] stores the vaule of the n-th OID"""
            try:
                errorIndication, errorStatus, errorIndex, varBinds = next(g)

                if errorIndication:
                    print(errorIndication)
                    break
                elif errorStatus:
                    print('%s at %s' % (errorStatus.prettyPrint(),
                                        errorIndex and varBinds[int(errorIndex) - 1][0] or '?'))
                    break
                else:
                    """Store data for selected ports"""
                    if index in self.ports.keys():
                        self.collector.feed_port_data(index, int(varBinds[0][1]), int(varBinds[1][1]))
            except StopIteration:
                break

    # Run the background thread task in the thread execution pool
    @run_on_executor
    def background_thread_task(self):
        """The the adapter background thread time loop.

        This method runs in the thread executor pool, sleeping for the specified interval until
        the background_thread_task_enable is set to false."""

        self.start_time=time.time()
        # Define initial delta as 0 by computing it in the snapshot wrt itself
        # self.snapshot = SnmpSnapshot(self.ports)
        self.snapshot = self.collector

        self.background_thread_task_enable = True ###

        while self.background_thread_task_enable: # execute in the background thread in the adapter
            self.fetch_all_packet_counts()
            self.collector.compute_delta(self.snapshot)
            
            """Correct the iteration interval for execution time"""
            snmpDelay = time.time() - self.start_time
            print(snmpDelay)
            print()
            time.sleep(max(0, self.samplingInterval - snmpDelay))

            self.start_time=time.time()
            self.snapshot, self.collector = self.collector, SnmpSnapshot(self.ports)
            self.snapshot.print()
            print(id(self.snapshot))
            print(id(self.collector))

        logging.debug("Background thread loop stopping")

        # pass on the snapshot object to the parameter tree in the adapter or
        # store values in a parameter tree inside the snapshot

class SnmpSnapshot:
    """Populate and store a single complete snapshot of packet flow rate data"""
    def __init__(self, ports):
        self.ports = ports
        self.packets = {}
        self.delta = {}
    
    def feed_port_data(self, port, inPackets, outPackets):
        self.packets[port] = {'inPackets' : inPackets, 'outPackets' : outPackets}

    def compute_delta(self, snapshot): 
        """Compute differentials and store them in the latest instance of the class"""
        for port in self.ports.keys():
            self.delta[port] = {
                'inPackets' :  (self.packets[port][ 'inPackets']
                  - snapshot.packets[port][ 'inPackets']),
                'outPackets' : (self.packets[port]['outPackets']
                  - snapshot.packets[port]['outPackets'])
            }

    def print(self):
        for port in self.ports.keys():
            print('Port: {} In: {} ΔIn: {} Out: {} ΔOut: {}'.format(
                port,
                self.packets[port]['inPackets'],
                self.delta[port]['inPackets'],
                self.packets[port]['outPackets'],
                self.delta[port]['outPackets']
                )
            )
        print()