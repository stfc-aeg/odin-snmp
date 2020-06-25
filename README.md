# SNMP Adapter for Odin Control Server

This This adapter provides ODIN clients with information about packet flow through a network network device - typically about data flowing through a switch from the detector
to the compute nodes. It obtains the data from the netework device via the SNMP protocol. The device is querried in regular time intervals for packet information on specific ports, according to parameters specified in the config file. The periodic loop providing the adapter with packet counts runs on a separate background thread, thus it does not affect performance of the ODIN Server.


## Config file

Below is the typical config file entry for the SNMP adapter:
```
[adapter.odin_snmp]
module = odin_snmp.adapter.SnmpAdapter
deviceName = devswitch5920
ports = 1:x-ray-detector, 3:compute-node-A, 4:compute-node-B
oids = ifInUcastPkts,ifOutUcastPkts
samplingInterval = 1
```
Constraints on the config parameters

**`module`**
This must always be set to odin_snmp.adapter.SnmpAdapter for the adapter to load.

**`deviceName`**
This must be specified, and has no default value if left empty.

**`ports`**
The value takes the comma separated valus in format `port_number:port_name`. Port_number is the port index as defined on the network device, port_name is user-defined and typically specifies the device connected to the particular port.
If left empty, the adapter will monitor packet flow on all detectable ports.

**`oids`**
This is a parameter passed to the SNMP protocol engine. `ifInUcastPkts,ifOutUcastPkts` is the recommended setting for monitorig the in and out traffic of UCAST packets.
If left empty, the adapter will default to these values.

**`samplingInterval`**
This parameter is specified as time, in seconds, and determines the time inervals of subsequent SNMP requests for packet information.
The adapter will default to 1s if smaller or no interval is specified. The reason for this is the latency of the SNMP protocol response. In lab tests that latency was measured to be in the region of 0.3 s.
This parameter can be dynamically changed via a PUT request while the adapter is operating.


## API-accessible parameters

The adapter can return the following parameters via a GET request:

**`total_packet_count`**
This is read-only dynamic parameter respresening a cumulative value of the total number of packets that have flown through all configured interfaces. This resets to zero once the maximum value permitted by the bit size is reached.

**`relative_packet_count`**
This is a read-only dynamic parameter respresenting delta values for all configured interfaces. Those are simply the differences between consequtive packet count values returned to the adapter.

**`ports`**
This is a static read-only parameter and returns the port configuration, either as specified in the config file. If none were configured, it returns the list of all available ports, which the adapter is monitoring.

**`interval`**
This is a dynamic parameter representing the time interval at which new requests for packet data are made. It's value is set by the config file parameter, and can be also later changed by the user.


## Source code classes

**`SnmpAdapter`**
Main class for loading the SNMP adapter, called by the ODIN server.

**`SnmpRequester`**
Class that handles the main functionality of the adapter, i.e. port detection and updating the packet count data.

**`SnmpSnapshot`**
Class for storage of packet count data, the core outcome of the adapter's operation.

## Issues

Maximum packet cout value delta anomaly: account for packet count values jumping from max to 0 in delta calculation.

Catch all errors associated with anomalous config file entries.