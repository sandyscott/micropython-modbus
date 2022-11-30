#!/usr/bin/env python
#
# Copyright (c) 2019, Pycom Limited.
#
# This software is licensed under the GNU GPL version 3 or any
# later version, with permitted additional terms. For more information
# see the Pycom Licence v1.0 document supplied with this file, or
# available at https://www.pycom.io/opensource/licensing
#

# system packages
# import random
import struct
import socket
import time

# custom packages
from . import functions
from . import const as Const
from .common import Request
from .common import ModbusException
from .modbus import Modbus
from urequests import request

# typing not natively supported on MicroPython
from .typing import List, Optional, Tuple, Union


class ModbusTCP(Modbus):
    def __init__(self):
        super().__init__(
            # set itf to TCPServer object, addr_list to None
            TCPServer(),
            None
        )

    def bind(self,
             local_ip: str,
             local_port: int = 502,
             max_connections: int = 10) -> None:
        self._itf.bind(local_ip, local_port, max_connections)

    def get_bound_status(self) -> bool:
        try:
            return self._itf.get_is_bound()
        except Exception:
            return False

    def process(self) -> bool:
        """
        Process the modbus requests.

        :returns:   Result of processing, True on success, False otherwise
        :rtype:     bool
        """
        reg_type = None
        req_type = None

        request = self._itf.get_request(unit_addr_list=self._addr_list,
                                        timeout=0)
        if request is None:
            return False

        if request.function == Const.READ_COILS:
            # Coils (setter+getter) [0, 1]
            # function 01 - read single register
            reg_type = 'COILS'
            req_type = 'READ'
        elif request.function == Const.READ_DISCRETE_INPUTS:
            # Ists (only getter) [0, 1]
            # function 02 - read input status (discrete inputs/digital input)
            reg_type = 'ISTS'
            req_type = 'READ'
        elif request.function == Const.READ_HOLDING_REGISTERS:
            # Hregs (setter+getter) [0, 65535]
            # function 03 - read holding register
            reg_type = 'HREGS'
            req_type = 'READ'
        elif request.function == Const.READ_INPUT_REGISTER:
            # Iregs (only getter) [0, 65535]
            # function 04 - read input registers
            reg_type = 'IREGS'
            req_type = 'READ'
        elif request.function == Const.WRITE_SINGLE_COIL:
            # Coils (setter+getter) [0, 1]
            # function 05 - write single register
            reg_type = 'COILS'
            req_type = 'WRITE'
        elif request.function == Const.WRITE_SINGLE_REGISTER:
            # Hregs (setter+getter) [0, 65535]
            # function 06 - write holding register
            reg_type = 'HREGS'
            req_type = 'WRITE'
        else:
            request.send_exception(Const.ILLEGAL_FUNCTION)

        if reg_type:
            if req_type == 'READ':
                self._process_read_access(request=request, reg_type=reg_type)
            elif req_type == 'WRITE':
                self._process_write_access(request=request, reg_type=reg_type)

        return True

    def _create_response(self, request: request, reg_type: str):
        """
        Create a response.

        :param      request:   The request
        :type       request:   request
        :param      reg_type:  The register type
        :type       reg_type:  str

        :returns:   Values of this register
        :rtype:     Union[bool, int, List[int], List[bool]]
        """
        if type(self._register_dict[reg_type][request.register_addr]) is list:
            return self._register_dict[reg_type][request.register_addr]
        else:
            return [self._register_dict[reg_type][request.register_addr]]

    def _process_read_access(self, request: request, reg_type: str) -> None:
        """
        Process read access to register

        :param      request:   The request
        :type       request:   request
        :param      reg_type:  The register type
        :type       reg_type:  str
        """
        if request.register_addr in self._register_dict[reg_type]:
            vals = self._create_response(request=request, reg_type=reg_type)
            request.send_response(vals)
        else:
            request.send_exception(Const.ILLEGAL_DATA_ADDRESS)

    def _process_write_access(self, request: request, reg_type: str) -> None:
        """
        Process write access to register

        :param      request:   The request
        :type       request:   request
        :param      reg_type:  The register type
        :type       reg_type:  str
        """
        address = request.register_addr
        val = 0
        valid_register = False

        if address in self._register_dict[reg_type]:
            if reg_type == 'COILS':
                val = request.data[0]
                if val == 0x00:
                    val = False
                    valid_register = True

                    request.send_response()

                    self.set_coil(address=address, value=val)
                elif val == 0xFF:
                    val = True
                    valid_register = True

                    request.send_response()

                    self.set_coil(address=address, value=val)
                else:
                    request.send_exception(Const.ILLEGAL_DATA_VALUE)
            elif reg_type == 'HREGS':
                valid_register = True
                val = request.data_as_registers(signed=False)[0]

                request.send_response()

                self.set_hreg(address=address, value=val)
            else:
                pass

            if valid_register:
                self._set_changed_register(reg_type=reg_type,
                                           address=address,
                                           value=val)
        else:
            request.send_exception(Const.ILLEGAL_DATA_ADDRESS)


class TCP(object):
    def __init__(self,
                 slave_ip: str,
                 slave_port: int = 502,
                 timeout: float = 5.0):
        self._sock = socket.socket()
        self.trans_id_ctr = 0

        # print(socket.getaddrinfo(slave_ip, slave_port))
        # [(2, 1, 0, '192.168.178.47', ('192.168.178.47', 502))]
        self._sock.connect(socket.getaddrinfo(slave_ip, slave_port)[0][-1])

        self._sock.settimeout(timeout)

    def _create_mbap_hdr(self,
                         slave_id: int,
                         modbus_pdu: bytes) -> Tuple[bytes, int]:
        """
        Create a Modbus header.

        :param      slave_id:    The slave identifier
        :type       slave_id:    int
        :param      modbus_pdu:  The modbus Protocol Data Unit
        :type       modbus_pdu:  bytes

        :returns:   Modbus header and unique transaction ID
        :rtype:     Tuple[bytes, int]
        """
        # only available on WiPy
        # trans_id = machine.rng() & 0xFFFF
        # use builtin function to generate random 24 bit integer
        # trans_id = random.getrandbits(24) & 0xFFFF
        # use incrementing counter as it's faster
        trans_id = self.trans_id_ctr
        self.trans_id_ctr += 1

        mbap_hdr = struct.pack(
            '>HHHB', trans_id, 0, len(modbus_pdu) + 1, slave_id)

        return mbap_hdr, trans_id

    def _bytes_to_bool(self, byte_list: bytes) -> List[bool]:
        bool_list = []
        for index, byte in enumerate(byte_list):
            bool_list.extend([bool(byte & (1 << n)) for n in range(8)])

        return bool_list

    def _to_short(self, byte_array: bytearray, signed: bool = True) -> bytes:
        response_quantity = int(len(byte_array) / 2)
        fmt = '>' + (('h' if signed else 'H') * response_quantity)

        return struct.unpack(fmt, byte_array)

    def _validate_resp_hdr(self,
                           response: bytearray,
                           trans_id: int,
                           slave_id: int,
                           function_code: int,
                           count: bool = False) -> bytes:
        """
        Validate the response header.

        :param      response:       The response
        :type       response:       bytearray
        :param      trans_id:       The transaction identifier
        :type       trans_id:       int
        :param      slave_id:       The slave identifier
        :type       slave_id:       int
        :param      function_code:  The function code
        :type       function_code:  int
        :param      count:          The count
        :type       count:          bool

        :returns:   Modbus response content
        :rtype:     bytes
        """
        rec_tid, rec_pid, rec_len, rec_uid, rec_fc = struct.unpack(
            '>HHHBB', response[:Const.MBAP_HDR_LENGTH + 1])

        if (trans_id != rec_tid):
            raise ValueError('wrong transaction Id')

        if (rec_pid != 0):
            raise ValueError('invalid protocol Id')

        if (slave_id != rec_uid):
            raise ValueError('wrong slave Id')

        if (rec_fc == (function_code + Const.ERROR_BIAS)):
            raise ValueError('slave returned exception code: {:d}'.
                             format(rec_fc))

        hdr_length = (Const.MBAP_HDR_LENGTH + 2) if count else \
            (Const.MBAP_HDR_LENGTH + 1)

        return response[hdr_length:]

    def _send_receive(self,
                      slave_id: int,
                      modbus_pdu: bytes,
                      count: bool) -> bytes:
        """
        Send a modbus message and receive the reponse.

        :param      slave_id:    The slave identifier
        :type       slave_id:    int
        :param      modbus_pdu:  The modbus PDU
        :type       modbus_pdu:  bytes
        :param      count:       The count
        :type       count:       bool

        :returns:   Modbus data
        :rtype:     bytes
        """
        mbap_hdr, trans_id = self._create_mbap_hdr(slave_id=slave_id,
                                                   modbus_pdu=modbus_pdu)
        self._sock.send(mbap_hdr + modbus_pdu)

        response = self._sock.recv(256)
        modbus_data = self._validate_resp_hdr(response=response,
                                              trans_id=trans_id,
                                              slave_id=slave_id,
                                              function_code=modbus_pdu[0],
                                              count=count)

        return modbus_data

    def read_coils(self,
                   slave_addr: int,
                   starting_addr: int,
                   coil_qty: int) -> List[bool, ...]:
        modbus_pdu = functions.read_coils(
            starting_address=starting_addr,
            quantity=coil_qty)

        response = self._send_receive(slave_id=slave_addr,
                                      modbus_pdu=modbus_pdu,
                                      count=True)
        status_pdu = self._bytes_to_bool(byte_list=response)

        return status_pdu

    def read_discrete_inputs(self,
                             slave_addr: int,
                             starting_addr: int,
                             input_qty: int) -> List[bool, ...]:
        modbus_pdu = functions.read_discrete_inputs(
            starting_address=starting_addr,
            quantity=input_qty)

        response = self._send_receive(slave_id=slave_addr,
                                      modbus_pdu=modbus_pdu,
                                      count=True)
        status_pdu = self._bytes_to_bool(byte_list=response)

        return status_pdu

    def read_holding_registers(self,
                               slave_addr: int,
                               starting_addr: int,
                               register_qty: int,
                               signed: bool = True) -> Tuple[int, ...]:
        modbus_pdu = functions.read_holding_registers(
            starting_address=starting_addr,
            quantity=register_qty)

        response = self._send_receive(slave_id=slave_addr,
                                      modbus_pdu=modbus_pdu,
                                      count=True)
        register_value = self._to_short(byte_array=response, signed=signed)

        return register_value

    def read_input_registers(self,
                             slave_addr: int,
                             starting_addr: int,
                             register_qty: int,
                             signed: int = True) -> Tuple[int, ...]:
        modbus_pdu = functions.read_input_registers(
            starting_address=starting_addr,
            quantity=register_qty)

        response = self._send_receive(slave_id=slave_addr,
                                      modbus_pdu=modbus_pdu,
                                      count=True)
        register_value = self._to_short(byte_array=response, signed=signed)

        return register_value

    def write_single_coil(self,
                          slave_addr: int,
                          output_address: int,
                          output_value: Union[int, bool]) -> bool:
        """
        Update a single coil.

        :param      slave_addr:      The slave address
        :type       slave_addr:      int
        :param      output_address:  The output address
        :type       output_address:  int
        :param      output_value:    The output value
        :type       output_value:    Union[int, bool]

        :returns:   Result of operation
        :rtype:     bool
        """
        modbus_pdu = functions.write_single_coil(output_address=output_address,
                                                 output_value=output_value)

        response = self._send_receive(slave_id=slave_addr,
                                      modbus_pdu=modbus_pdu,
                                      count=False)
        if response is None:
            return False

        operation_status = functions.validate_resp_data(
            data=response,
            function_code=Const.WRITE_SINGLE_COIL,
            address=output_address,
            value=output_value,
            signed=False)

        return operation_status

    def write_single_register(self,
                              slave_addr: int,
                              register_address: int,
                              register_value: int,
                              signed: bool = True) -> bool:
        modbus_pdu = functions.write_single_register(
            register_address=register_address,
            register_value=register_value,
            signed=signed)

        response = self._send_receive(slave_id=slave_addr,
                                      modbus_pdu=modbus_pdu,
                                      count=False)
        operation_status = functions.validate_resp_data(
            data=response,
            function_code=Const.WRITE_SINGLE_REGISTER,
            address=register_address,
            value=register_value,
            signed=signed)

        return operation_status

    def write_multiple_coils(self,
                             slave_addr: int,
                             starting_address: int,
                             output_values: List[int, bool]) -> bool:
        modbus_pdu = functions.write_multiple_coils(
            starting_address=starting_address,
            value_list=output_values)

        response = self._send_receive(slave_id=slave_addr,
                                      modbus_pdu=modbus_pdu,
                                      count=False)
        operation_status = functions.validate_resp_data(
            data=response,
            function_code=Const.WRITE_MULTIPLE_COILS,
            address=starting_address,
            quantity=len(output_values))

        return operation_status

    def write_multiple_registers(self,
                                 slave_addr: int,
                                 starting_address: int,
                                 register_values: List[int],
                                 signed=True) -> bool:
        modbus_pdu = functions.write_multiple_registers(
            starting_address=starting_address,
            register_values=register_values,
            signed=signed)

        response = self._send_receive(slave_id=slave_addr,
                                      modbus_pdu=modbus_pdu,
                                      count=False)
        operation_status = functions.validate_resp_data(
            data=response,
            function_code=Const.WRITE_MULTIPLE_REGISTERS,
            address=starting_address,
            quantity=len(register_values),
            # this fixes
            # https://github.com/brainelectronics/micropython-modbus/issues/23
            # signed=signed
        )

        return operation_status


class TCPServer(object):
    def __init__(self):
        self._sock = None
        self._client_sock = None
        self._is_bound = False

    def get_is_bound(self) -> bool:
        return self._is_bound

    def bind(self,
             local_ip: str,
             local_port: int = 502,
             max_connections: int = 10):
        if self._client_sock:
            self._client_sock.close()

        if self._sock:
            self._sock.close()

        self._sock = socket.socket()

        # print(socket.getaddrinfo(local_ip, local_port))
        # [(2, 1, 0, '192.168.178.47', ('192.168.178.47', 502))]
        self._sock.bind(socket.getaddrinfo(local_ip, local_port)[0][-1])

        self._sock.listen(max_connections)

        self._is_bound = True

    def _send(self, modbus_pdu: bytes, slave_addr: int) -> None:
        size = len(modbus_pdu)
        fmt = 'B' * size
        adu = struct.pack('>HHHB' + fmt, self._req_tid, 0, size + 1, slave_addr, *modbus_pdu)
        self._client_sock.send(adu)

    def send_response(self,
                      slave_addr: int,
                      function_code: int,
                      request_register_addr: int,
                      request_register_qty: int,
                      request_data: list,
                      values: Optional[list] = None,
                      signed: bool = True) -> None:
        modbus_pdu = functions.response(function_code,
                                        request_register_addr,
                                        request_register_qty,
                                        request_data,
                                        values,
                                        signed)
        self._send(modbus_pdu, slave_addr)

    def send_exception_response(self,
                                slave_addr: int,
                                function_code: int,
                                exception_code: int) -> None:
        modbus_pdu = functions.exception_response(function_code,
                                                  exception_code)
        self._send(modbus_pdu, slave_addr)

    def _accept_request(self,
                        accept_timeout: float,
                        unit_addr_list: list) -> None:
        self._sock.settimeout(accept_timeout)
        new_client_sock = None

        try:
            new_client_sock, client_address = self._sock.accept()
        except OSError as e:
            if e.args[0] != 11:     # 11 = timeout expired
                raise e

        if new_client_sock is not None:
            if self._client_sock is not None:
                self._client_sock.close()

            self._client_sock = new_client_sock

            # recv() timeout, setting to 0 might lead to the following error
            # "Modbus request error: [Errno 11] EAGAIN"
            # This is a socket timeout error
            self._client_sock.settimeout(0.5)

        if self._client_sock is not None:
            try:
                req = self._client_sock.recv(128)

                if len(req) == 0:
                    return None

                req_header_no_uid = req[:Const.MBAP_HDR_LENGTH - 1]
                self._req_tid, req_pid, req_len = struct.unpack('>HHH', req_header_no_uid)
                req_uid_and_pdu = req[Const.MBAP_HDR_LENGTH - 1:Const.MBAP_HDR_LENGTH + req_len - 1]
            except OSError:
                # MicroPython raises an OSError instead of socket.timeout
                # print("Socket OSError aka TimeoutError: {}".format(e))
                return None
            except Exception:
                # print("Modbus request error:", e)
                self._client_sock.close()
                self._client_sock = None
                return None

            if (req_pid != 0):
                # print("Modbus request error: PID not 0")
                self._client_sock.close()
                self._client_sock = None
                return None

            if ((unit_addr_list is not None) and (req_uid_and_pdu[0] not in unit_addr_list)):
                return None

            try:
                return Request(self, req_uid_and_pdu)
            except ModbusException as e:
                self.send_exception_response(req[0],
                                             e.function_code,
                                             e.exception_code)
                return None

    def get_request(self, unit_addr_list=None, timeout=None) -> None:
        if self._sock is None:
            raise Exception('Modbus TCP server not bound')

        if timeout > 0:
            start_ms = time.ticks_ms()
            elapsed = 0
            while True:
                if self._client_sock is None:
                    accept_timeout = None if timeout is None else (timeout - elapsed) / 1000
                else:
                    accept_timeout = 0
                req = self._accept_request(accept_timeout, unit_addr_list)
                if req:
                    return req
                elapsed = time.ticks_diff(start_ms, time.ticks_ms())
                if elapsed > timeout:
                    return None
        else:
            return self._accept_request(0, unit_addr_list)
