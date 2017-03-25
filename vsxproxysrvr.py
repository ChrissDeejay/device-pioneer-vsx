__author__ = 'scdozier'
## Pioneer VSX Receiver Proxy Server
##
## Written by csdozier@gmail.com
##
## This code is under the terms of the GPL v3 license.
## Based on alarm server https://github.com/juggie/AlarmServer/tree/smartthings (GPL v3)


import asyncore, asynchat
import ConfigParser
import datetime
import os, socket, string, sys, httplib, urllib, urlparse, ssl
import StringIO, mimetools
import json
import hashlib
import time
import getopt
import requests
from threading import Thread
import traceback
from threading import Thread
import base64
import logging
import logging.handlers



LOGTOFILE = False


def dict_merge(a, b):
    c = a.copy()
    c.update(b)
    return c


log = logging.getLogger('root')

def dict_merge(a, b):
    c = a.copy()
    c.update(b)
    return c

def start_logger(configfile):
    FORMAT = "%(asctime)-15s [%(filename)s:%(funcName)1s()] - %(levelname)s - %(message)s"
    logging.basicConfig(format=FORMAT)
    log.setLevel(logging.DEBUG)
    if LOGTOFILE:
        handler = logging.handlers.RotatingFileHandler(config.LOGFILE,
                                               maxBytes=2000000,
                                               backupCount=2,
                                               )
        formatter = logging.Formatter(FORMAT)
        handler.setFormatter(formatter)
        log.addHandler(handler)
    log.info('Logging started..')

def logger(message, level = 'info',type = 0):
    if 'info' in level or level == 0:
        log.info(message)
    elif 'error' in level:
        log.error(message)
    elif 'debug' in level:
        log.debug(message)

class VSXProxyServerConfig():
    def __init__(self, configfile):

        self._config = ConfigParser.ConfigParser()
        self._config.read(configfile)

        self.LOGFILE = self.read_config_var('main', 'logfile', '', 'str')
        self.LOGURLREQUESTS = self.read_config_var('main', 'logurlrequests', True, 'bool')
        self.PORT = self.read_config_var('main', 'port', 443, 'int')
        self.USETLS = self.read_config_var('main', 'use_tls', False, 'bool')
        self.CERTFILE = self.read_config_var('main', 'certfile', 'server.crt', 'str')
        self.KEYFILE = self.read_config_var('main', 'keyfile', 'server.key', 'str')
        self.RECEIVERIP = self.read_config_var('receiver', 'host', '', 'str')
        self.RECEIVERPORT = self.read_config_var('receiver', 'port', 8102, 'int')
        self.VOLUMELIMIT = self.read_config_var('receiver', 'volume_limit', 0, 'int')
        self.CALLBACKURL_BASE = self.read_config_var('main', 'callbackurl_base', '', 'str')
        self.CALLBACKURL_APP_ID = self.read_config_var('main', 'callbackurl_app_id', '', 'str')
        self.CALLBACKURL_ACCESS_TOKEN = self.read_config_var('main', 'callbackurl_access_token', '', 'str')
        self.CALLBACKURL_MAIN_DEVICE_ID = self.read_config_var('main', 'callbackurl_main_zone_device_id', '', 'str')
        self.CALLBACKURL_HDZ_DEVICE_ID = self.read_config_var('main', 'callbackurl_hdz_zone_device_id', '', 'str')
        self.CALLBACKURL_HDZ_DEVICE_ID = self.read_config_var('main', 'callbackurl_hdz_zone_device_id', '', 'str')
        self.MAIN_INPUTS = self.read_config_sec('main_inputs')
        self.HDZ_INPUTS = self.read_config_sec('hdz_inputs')



        global LOGTOFILE
        if self.LOGFILE == '':
            LOGTOFILE = False
        else:
            LOGTOFILE = True

        self.INPUTNAMES={}
        for i in (24,53,06,25,02,44,41,46,38,33,17,23,22,21,20,19):
            self.INPUTNAMES[i]=self.read_config_var('inputs', str(i), False, 'str', True)


    def defaulting(self, section, variable, default, quiet = False):
        if quiet == False:
            print('Config option '+ str(variable) + ' not set in ['+str(section)+'] defaulting to: \''+str(default)+'\'')

    def read_config_var(self, section, variable, default, type = 'str', quiet = False):
        try:
            if type == 'str':
                return self._config.get(section,variable)
            elif type == 'bool':
                return self._config.getboolean(section,variable)
            elif type == 'int':
                return int(self._config.get(section,variable))
        except (ConfigParser.NoSectionError, ConfigParser.NoOptionError):
            self.defaulting(section, variable, default, quiet)
            return default
    def read_config_sec(self, section):
        try:
            return self._config._sections[section]
        except (ConfigParser.NoSectionError, ConfigParser.NoOptionError):
            return {}


class HTTPChannel(asynchat.async_chat):
    def __init__(self, server, sock, addr):
        asynchat.async_chat.__init__(self, sock)
        self.server = server
        self.set_terminator("\r\n\r\n")
        self.header = None
        self.data = ""
        self.shutdown = 0

    def collect_incoming_data(self, data):
        self.data = self.data + data
        if len(self.data) > 16384:
        # limit the header size to prevent attacks
            self.shutdown = 1

    def found_terminator(self):
        if not self.header:
            # parse http header
            fp = StringIO.StringIO(self.data)
            request = string.split(fp.readline(), None, 2)
            if len(request) != 3:
                # badly formed request; just shut down
                self.shutdown = 1
            else:
                # parse message header
                self.header = mimetools.Message(fp)
                self.set_terminator("\r\n")
                self.server.handle_request(
                    self, request[0], request[1], self.header
                    )
                self.close_when_done()
            self.data = ""
        else:
            pass # ignore body data, for now

    def pushstatus(self, status, explanation="OK"):
        self.push("HTTP/1.0 %d %s\r\n" % (status, explanation))

    def pushok(self, content):
        self.pushstatus(200, "OK")
        self.push('Content-type: application/json\r\n')
        self.push('Expires: Sat, 26 Jul 1997 05:00:00 GMT\r\n')
        self.push('Last-Modified: '+ datetime.datetime.now().strftime("%d/%m/%Y %H:%M:%S")+' GMT\r\n')
        self.push('Cache-Control: no-store, no-cache, must-revalidate\r\n' )
        self.push('Cache-Control: post-check=0, pre-check=0\r\n')
        self.push('Pragma: no-cache\r\n' )
        self.push('\r\n')
        self.push(content)

    def pushfile(self, file):
        self.pushstatus(200, "OK")
        extension = os.path.splitext(file)[1]
        if extension == ".html":
            self.push("Content-type: text/html\r\n")
        elif extension == ".js":
            self.push("Content-type: text/javascript\r\n")
        elif extension == ".png":
            self.push("Content-type: image/png\r\n")
        elif extension == ".css":
            self.push("Content-type: text/css\r\n")
        self.push("\r\n")
        self.push_with_producer(push_FileProducer(sys.path[0] + os.sep + 'ext' + os.sep + file))

class VSXControl(asynchat.async_chat):
    current_main_input = ''
    current_hdz_input = ''
    current_main_power = False
    current_hdz_power = False
    current_main_level = '0'
    current_hdz_level = '0'
    current_main_mute = False
    current_hdz_mute = False

    last_command = ''
    last_command_time = datetime.datetime.now()

    def __init__(self, config):
        # Call parent class's __init__ method
        asynchat.async_chat.__init__(self)

        # Define some private instance variables
        self._buffer = []

        # Are we logged in?
        self._loggedin = False

        # Set our terminator to \n
        self.set_terminator("\r\n")

        # Set config
        self._config = config

        # Reconnect delay
        self._retrydelay = 10

        self.do_connect()

    def do_connect(self, reconnect = False):
        # Create the socket and connect to the server
        if reconnect == True:
            logger('Connection failed, retrying in '+str(self._retrydelay)+ ' seconds')
            for i in range(0, self._retrydelay):
                time.sleep(1)

        self.create_socket(socket.AF_INET, socket.SOCK_STREAM)

        self.connect((self._config.RECEIVERIP, self._config.RECEIVERPORT))

    def collect_incoming_data(self, data):
        # Append incoming data to the buffer
        self._buffer.append(data)

    def found_terminator(self):
        line = "".join(self._buffer)
        self.handle_line(line)
        self._buffer = []

    def handle_connect(self):
        self._loggedin = True
        self._buffer = []
        logger("Connected to %s:%i" % (self._config.RECEIVERIP, self._config.RECEIVERPORT))
        if len(self.last_command) > 0:
            self.send_command(self.last_command)
            self.last_command = ''

    def handle_close(self):
        self._loggedin = False
        self.close()
        logger("Disconnected from %s:%i" % (self._config.RECEIVERIP, self._config.RECEIVERPORT))
        self.do_connect(True)

    def handle_error(self):
        self._loggedin = False
        self.close()
        logger("Error, disconnected from %s:%i" % (self._config.RECEIVERIP, self._config.RECEIVERPORT))
        self.do_connect(True)

    def send_command(self, command):
        logger('TX > '+command)
        self.last_command = command
        self.push(command+'\r\n')
        time.sleep(0.085) #sleep 85ms between commands

    def handle_line(self, input):
        if input != '':
            #for client in CONNECTEDCLIENTS:
            #    CONNECTEDCLIENTS[client].send_command(input, False)

            #code=int(input[:3])
            #parameters=input[3:][:-2]
            #event = getMessageType(int(code))
            #message = self.format_event(event, parameters)
            main_zone_URL_prefix = self._config.CALLBACKURL_BASE + "/" + self._config.CALLBACKURL_APP_ID + "/vsxreceiver/" + str(self._config.CALLBACKURL_MAIN_DEVICE_ID) + "/"
            main_zone_URL_suffix = "?access_token=" + self._config.CALLBACKURL_ACCESS_TOKEN
            hdz_zone_URL_prefix = self._config.CALLBACKURL_BASE + "/" + self._config.CALLBACKURL_APP_ID + "/vsxreceiver/" + str(self._config.CALLBACKURL_HDZ_DEVICE_ID) + "/"
            hdz_zone_URL_suffix = "?access_token=" + self._config.CALLBACKURL_ACCESS_TOKEN
            logger('RX < ' +str(input))
            input = str(input)
            #/vsxreceiver/:id/:command/:state
            #/vsxreceiver/:id/power/<on|off>
            #/vsxreceiver/:id/input/<inputname>
            #/vsxreceiver/:id/volumeset/<level 0-100>
            #/vsxreceiver/:id/mute/<on|off>
            try:
                if input.startswith("PWR"): #main power on/off
                    if input.endswith('0'):
                        if not self.current_main_power:
                            logger('TX > HTTP GET: '+main_zone_URL_prefix+'power/on'+main_zone_URL_suffix)
                            my_requests_thread = RequestsThread(main_zone_URL_prefix+'power/on',access_token=self._config.CALLBACKURL_ACCESS_TOKEN)
                            my_requests_thread.start()
                        self.current_main_power = True
                    if input.endswith('1'):
                        if self.current_main_power:
                            logger('TX > HTTP GET:'+main_zone_URL_prefix+'power/off'+main_zone_URL_suffix)
                            my_requests_thread = RequestsThread(main_zone_URL_prefix+'power/off',access_token=self._config.CALLBACKURL_ACCESS_TOKEN)
                            my_requests_thread.start()
                        self.current_main_power = False
                elif input.startswith("ZEP"): #hdz power on/off
                    if input.endswith('0'):
                        if not self.current_hdz_power:
                            logger('TX > HTTP GET:'+hdz_zone_URL_prefix+'power/on'+hdz_zone_URL_suffix)
                            my_requests_thread = RequestsThread(hdz_zone_URL_prefix+'power/on',access_token=self._config.CALLBACKURL_ACCESS_TOKEN)
                            my_requests_thread.start()
                        self.current_hdz_power = True
                    if input.endswith('1'):
                        if self.current_hdz_power:
                            logger('TX > HTTP GET:'+hdz_zone_URL_prefix+'power/off'+hdz_zone_URL_suffix)
                            my_requests_thread = RequestsThread(hdz_zone_URL_prefix+'power/off',access_token=self._config.CALLBACKURL_ACCESS_TOKEN)
                            my_requests_thread.start()
                        self.current_hdz_power = False
                elif input.startswith("VOL"): #main volume
                    code = input.split('VOL')[1]
                    vol_db = ((float(code) - 161) / 2)
                    scaledValue = int(0 + (vol_db - -80) * (100 - 0) / (int(config.VOLUMELIMIT) - -80))
                    if self.current_main_level != str(scaledValue):
                        logger('TX > HTTP GET:'+main_zone_URL_prefix+'volumeset/'+str(scaledValue)+main_zone_URL_suffix)
                        my_requests_thread = RequestsThread(main_zone_URL_prefix+'volumeset/'+str(scaledValue),access_token=self._config.CALLBACKURL_ACCESS_TOKEN)
                        my_requests_thread.start()
                    self.current_main_level = str(scaledValue)
                elif input.startswith("XV"): #hdz volume
                    code = input.split('XV')[1]
                    vol_db = ((float(code) - 81))
                    scaledValue = int(0 + (vol_db - -80) * (100 - 0) / (int(config.VOLUMELIMIT) - -80))
                    if self.current_hdz_level != str(scaledValue):
                        logger('TX > HTTP GET:'+hdz_zone_URL_prefix+'volumeset/'+str(scaledValue)+hdz_zone_URL_suffix)
                        my_requests_thread = RequestsThread(hdz_zone_URL_prefix+'volumeset/'+str(scaledValue),access_token=self._config.CALLBACKURL_ACCESS_TOKEN)
                        my_requests_thread.start()
                    self.current_hdz_level = str(scaledValue)
                elif input.startswith("MUT"): #main mute on/off
                    if input.endswith('0'):
                        if not self.current_main_mute:
                            logger('TX > HTTP GET: '+main_zone_URL_prefix+'mute/on'+main_zone_URL_suffix)
                            my_requests_thread = RequestsThread(main_zone_URL_prefix+'mute/on',access_token=self._config.CALLBACKURL_ACCESS_TOKEN)
                            my_requests_thread.start()
                        self.current_main_mute = True
                    if input.endswith('1'):
                        if self.current_main_mute:
                            logger('TX > HTTP GET:'+main_zone_URL_prefix+'mute/off'+main_zone_URL_suffix)
                            my_requests_thread = RequestsThread(main_zone_URL_prefix+'mute/off',access_token=self._config.CALLBACKURL_ACCESS_TOKEN)
                            my_requests_thread.start()
                        self.current_main_mute = False
                elif input.startswith("HZMUT"): #hdz mute on/off
                    if input.endswith('0'):
                        if not self.current_hdz_mute:
                            logger('TX > HTTP GET:'+hdz_zone_URL_prefix+'mute/on'+hdz_zone_URL_suffix)
                            my_requests_thread = RequestsThread(hdz_zone_URL_prefix+'mute/on',access_token=self._config.CALLBACKURL_ACCESS_TOKEN)
                            my_requests_thread.start()
                        self.current_hdz_mute = True
                    if input.endswith('1'):
                        if self.current_hdz_mute:
                            logger('TX > HTTP GET:'+hdz_zone_URL_prefix+'mute/off'+hdz_zone_URL_suffix)
                            my_requests_thread = RequestsThread(hdz_zone_URL_prefix+'mute/off',access_token=self._config.CALLBACKURL_ACCESS_TOKEN)
                            my_requests_thread.start()
                        self.current_hdz_mute = False
                elif input.startswith("FN"): #main input
                    code = input.split('FN')[1]
                    if len(code) ==2:
                        if self.current_main_input != str(self._config.MAIN_INPUTS[str(code)]):
                            logger('TX > HTTP GET:'+main_zone_URL_prefix+'input/'+str(self._config.MAIN_INPUTS[str(code)])+main_zone_URL_suffix)
                            my_requests_thread = RequestsThread(main_zone_URL_prefix+'input/'+str(self._config.MAIN_INPUTS[str(code)]),access_token=self._config.CALLBACKURL_ACCESS_TOKEN)
                            my_requests_thread.start()
                        self.current_main_input = str(self._config.MAIN_INPUTS[str(code)])
                    if '02' in str(code):
                        self.send_command('?FR')
                elif input.startswith("ZEA"): #hdz input
                    code = input.split('ZEA')[1]
                    if len(code) ==2:
                        if self.current_hdz_input != str(self._config.HDZ_INPUTS[str(code)]):
                            logger('TX > HTTP GET:'+hdz_zone_URL_prefix+'input/'+str(self._config.HDZ_INPUTS[str(code)])+hdz_zone_URL_suffix)
                            my_requests_thread = RequestsThread(hdz_zone_URL_prefix+'input/'+str(self._config.HDZ_INPUTS[str(code)]),access_token=self._config.CALLBACKURL_ACCESS_TOKEN)
                            my_requests_thread.start()
                        self.current_hdz_input = str(self._config.HDZ_INPUTS[str(code)])
                elif input.startswith("GEH01020"): #main track
                    track = input.split('GEH01020"')[1].split('"')[0]
                    if self.current_main_power:
                        logger('TX > HTTP GET:'+main_zone_URL_prefix+'track/'+str(self.current_main_input+': '+track)+main_zone_URL_suffix)
                        my_requests_thread = RequestsThread(main_zone_URL_prefix+'track/'+str(self.current_main_input+': '+track),access_token=self._config.CALLBACKURL_ACCESS_TOKEN)
                        my_requests_thread.start()
                    else:
                        logger('TX > HTTP GET:'+hdz_zone_URL_prefix+'track/'+str(self.current_hdz_input+': '+track)+hdz_zone_URL_suffix)
                        my_requests_thread = RequestsThread(hdz_zone_URL_prefix+'track/'+str(self.current_hdz_input+': '+track),access_token=self._config.CALLBACKURL_ACCESS_TOKEN)
                        my_requests_thread.start()
                    if self.current_hdz_input == self.current_main_input and self.current_hdz_power:
                        logger('TX > HTTP GET:'+hdz_zone_URL_prefix+'track/'+str(self.current_hdz_input+': '+track)+hdz_zone_URL_suffix)
                        my_requests_thread = RequestsThread(hdz_zone_URL_prefix+'track/'+str(self.current_hdz_input+': '+track),access_token=self._config.CALLBACKURL_ACCESS_TOKEN)
                        my_requests_thread.start()
                elif input.startswith("GCH03010100000000100"):
                    logger('TX > HTTP GET:'+main_zone_URL_prefix+'pause'+main_zone_URL_suffix)
                    my_requests_thread = RequestsThread(main_zone_URL_prefix+'pause'+main_zone_URL_suffix,access_token=self._config.CALLBACKURL_ACCESS_TOKEN)
                    my_requests_thread.start()
                    if self.current_hdz_input == self.current_main_input and self.current_hdz_power:
                        logger('TX > HTTP GET:'+hdz_zone_URL_prefix+'pause'+hdz_zone_URL_suffix)
                        my_requests_thread = RequestsThread(hdz_zone_URL_prefix+'pause'+hdz_zone_URL_suffix,access_token=self._config.CALLBACKURL_ACCESS_TOKEN)
                        my_requests_thread.start()
                elif input.startswith("FRF"): #fm station
                    freq_string = input.split('FRF')[1]
                    track = str(int(freq_string[0:3]))+'.'+freq_string[-2:]
                    logger('TX > HTTP GET:'+main_zone_URL_prefix+'track/'+str(self.current_main_input+': '+track)+main_zone_URL_suffix)
                    my_requests_thread = RequestsThread(main_zone_URL_prefix+'track/'+str(self.current_main_input+': '+track),access_token=self._config.CALLBACKURL_ACCESS_TOKEN)
                    my_requests_thread.start()
                elif input.startswith("PRA"): #tuner preset
                    self.send_command('?FR')
            except Exception as ex:
                tb = traceback.format_exc()
                logger('Exception! '+ str(ex.message)+str(tb))

class push_FileProducer:
    # a producer which reads data from a file object

    def __init__(self, file):
        self.file = open(file, "rb")

    def more(self):
        if self.file:
            data = self.file.read(2048)
            if data:
                return data
            self.file = None
        return ""

class VSXProxyServer(asyncore.dispatcher):

    def __init__(self, config):
        # Call parent class's __init__ method
        asyncore.dispatcher.__init__(self)

        # Create VSX Receiver Control object
        self._VSXControl = VSXControl(config)

        #Store config
        self._config = config

        # Create socket and listen on it
        self.create_socket(socket.AF_INET, socket.SOCK_STREAM)
        self.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.bind(("", config.PORT))
        self.listen(5)
        logger('Listening for HTTP(S) connections on port: '+str(config.PORT))

        #Start Status Poller
        _vsxstatuspoller = VSXStatusPoller(self._VSXControl)
        _vsxstatuspoller.start()

    def handle_accept(self):
        # Accept the connection
        conn, addr = self.accept()
        if (config.LOGURLREQUESTS):
            logger('Incoming web connection from %s' % repr(addr))

        try:
            if config.USETLS:
                HTTPChannel(self, ssl.wrap_socket(conn, server_side=True, certfile=config.CERTFILE, keyfile=config.KEYFILE, ssl_version=ssl.PROTOCOL_TLSv1), addr)
            else:
                HTTPChannel(self, conn, addr) #use non ssl
        except ssl.SSLError:
            return

    def handle_request(self, channel, method, request, header):
        if (config.LOGURLREQUESTS):
            logger('Web request: '+str(method)+' '+str(request))

        query = urlparse.urlparse(request)
        query_array = urlparse.parse_qs(query.query, True)
        path = query.path
        try:
            if '&apiserverurl' in query.path:
                path,base64url = query.path.split('&apiserverurl=')
                url = urllib.unquote(base64url).decode('utf8')
                if url not in config.CALLBACKURL_BASE:
                    url = url.replace('http:','https:')
                    logger('Setting API Base URL To: '+url)
                    config.CALLBACKURL_BASE = url
            logger(path)
            if self._VSXControl._loggedin == False:
                channel.pushstatus(500, "Not Connected to Receiver, try again later.")
                logger("500 Error, Unable to process request as connection to receiver is down.")
            elif path == '/':
                channel.pushstatus(404, "Not found")
            elif '/pioneervsxcontrol/main/power' in path:  #power on/off main zone
                if path.split('/')[-1] == 'on':
                    channel.pushok(json.dumps({'response' : 'Powering on receiver'}))
                    self._VSXControl.send_command('PO')
                if path.split('/')[-1] == 'off':
                    channel.pushok(json.dumps({'response' : 'Powering off receiver'}))
                    self._VSXControl.send_command('PF')
            elif '/pioneervsxcontrol/hdz/power' in path:  #power on/off hd zone
                if path.split('/')[-1] == 'on':
                    channel.pushok(json.dumps({'response' : 'Powering on receiver'}))
                    self._VSXControl.send_command('ZEO')
                if path.split('/')[-1] == 'off':
                    channel.pushok(json.dumps({'response' : 'Powering off receiver'}))
                    self._VSXControl.send_command('ZEF')
            elif '/pioneervsxcontrol/main/volumeset' in path:  #main zone volume set
                try:
                    level = int(path.split('/')[-1])
                    if level <= 100 and level >= 0:
                        db_value = -80 + ((level*config.VOLUMELIMIT)/100)+((4*level)/5)
                        code = ((db_value*2)+161)
                        if len(str(code)) is 2:
                            self._VSXControl.send_command('0'+str(code)+'VL')
                        elif len(str(code)) is 1:
                            self._VSXControl.send_command('00'+str(code)+'VL')
                        else:
                            self._VSXControl.send_command(str(code)+'VL')
                except ValueError:
                    logger ('Invalid volume level received')
            elif '/pioneervsxcontrol/hdz/volumeset' in path:  #hdz zone volume set
                try:
                    level = int(path.split('/')[-1])
                    if level <= 100 and level >= 0:
                        db_value = -80 + ((level*config.VOLUMELIMIT)/100)+((4*level)/5)
                        code = ((db_value)+81)
                        self._VSXControl.send_command(str(code)+'HZV')

                except ValueError:
                    logger ('Invalid volume level received')
            elif '/pioneervsxcontrol/main/mute' in path:  #power on/off main zone
                if path.split('/')[-1] == 'on':
                    channel.pushok(json.dumps({'response' : 'Mute on'}))
                    self._VSXControl.send_command('MO')
                if path.split('/')[-1] == 'off':
                    channel.pushok(json.dumps({'response' : 'Mute off'}))
                    self._VSXControl.send_command('MF')
            elif '/pioneervsxcontrol/hdz/mute' in path:  #hdz on/off main zone
                if path.split('/')[-1] == 'on':
                    channel.pushok(json.dumps({'response' : 'Mute on'}))
                    self._VSXControl.send_command('HZMO')
                if path.split('/')[-1] == 'off':
                    channel.pushok(json.dumps({'response' : 'Mute off'}))
                    self._VSXControl.send_command('HZMF')
            elif '/pioneervsxcontrol/main/input/set' in path:  #set input main zone
                code = path.split('/')[-1]
                channel.pushok(json.dumps({'response' : 'Setting input:'+str(code)}))
                self._VSXControl.send_command(str(code)+'FN')
            elif '/pioneervsxcontrol/hdz/input/set' in path:  #set input hdz zone
                code = path.split('/')[-1]
                channel.pushok(json.dumps({'response' : 'Setting input:'+str(code)}))
                self._VSXControl.send_command(str(code)+'ZEA')
            elif '/pioneervsxcontrol/main/input' in path:  #input next/prev main zone
                if path.split('/')[-1] == 'next':
                    channel.pushok(json.dumps({'response' : 'Next Input'}))
                    self._VSXControl.send_command('FU')
                if path.split('/')[-1] == 'previous':
                    channel.pushok(json.dumps({'response' : 'Previous Input'}))
                    self._VSXControl.send_command('FD')
            elif '/pioneervsxcontrol/hdz/input' in path:  #input next/prev hdz zone
                if path.split('/')[-1] == 'next':
                    channel.pushok(json.dumps({'response' : 'Next Input'}))
                    self._VSXControl.send_command('ZEC')
                if path.split('/')[-1] == 'previous':
                    channel.pushok(json.dumps({'response' : 'Previous Input'}))
                    self._VSXControl.send_command('ZEB')
            elif '/pioneervsxcontrol/main/refresh' in path:  #refresh main zone
                    self._VSXControl.send_command('?P')
                    self._VSXControl.send_command('?V')
                    self._VSXControl.send_command('?F')
            elif '/pioneervsxcontrol/hdz/refresh' in path:  #refresh hdz zone
                    self._VSXControl.send_command('?ZEP')
                    self._VSXControl.send_command('?ZEA')
                    self._VSXControl.send_command('?HZV')
            elif '/pioneervsxcontrol/main/tuner' in path:  #input next/prev main zone
                if path.split('/')[-1] == 'next':
                    channel.pushok(json.dumps({'response' : 'Next Input'}))
                    self._VSXControl.send_command('TPI')
                if path.split('/')[-1] == 'previous':
                    channel.pushok(json.dumps({'response' : 'Previous Input'}))
                    self._VSXControl.send_command('TPD')
            else:
                channel.pushstatus(404, "Not found")
                channel.push("Content-type: text/html\r\n")
                channel.push("\r\n")
        except Exception as ex:
            tb = traceback.format_exc()
            logger('Exception! '+ str(ex.message)+str(tb))

class RequestsThread(Thread):
    def __init__(self,url,method='get',access_token=''):
        super(RequestsThread, self).__init__()
        """Initialize"""
        self.url = url
        self.method = method
        self.access_token = access_token
        self.daemon = True
    def run(self):
        headers = {'Authorization': 'Bearer {}'.format(self.access_token)}
        try:
            if 'get' in self.method:
                requests.get(self.url,timeout=20,headers=headers)
        except Exception as ex:
            tb = traceback.format_exc()
            logger('Exception! '+ str(ex.message)+str(tb)+'url:'+self.url)


class VSXStatusPoller(Thread):

    def __init__(self, vsxcontrol,poll_interval=2000):
        super(VSXStatusPoller, self).__init__()
        # Create VSX Receiver Control object
        self._vsxcontrol = vsxcontrol
        self.poll_interval = poll_interval
        self.daemon = True

    def run(self):
        while 1:
            try:
                #poll receiver for current values
                if self._vsxcontrol._loggedin:
                    self._vsxcontrol.send_command('?P')
                    self._vsxcontrol.send_command('?V')
                    self._vsxcontrol.send_command('?F')
                    self._vsxcontrol.send_command('?ZEP')
                    self._vsxcontrol.send_command('?ZEA')
                    self._vsxcontrol.send_command('?HZV')
                #sleep
                logger('Polling sleeping for '+str(self.poll_interval)+' seconds..')
                time.sleep(self.poll_interval)
            except Exception as ex:
                tb = traceback.format_exc()
                logger('Exception! '+ str(ex.message)+str(tb))



def usage():
    print 'Usage: '+sys.argv[0]+' -c <configfile>'

def main(argv):
    try:
      opts, args = getopt.getopt(argv, "hc:", ["help", "config="])
    except getopt.GetoptError:
        usage()
        sys.exit(2)
    for opt, arg in opts:
        if opt in ("-h", "--help"):
            usage()
            sys.exit()
        elif opt in ("-c", "--config"):
            global conffile
            conffile = arg


if __name__=="__main__":
    conffile='vsxproxysrvr.cfg'
    main(sys.argv[1:])
    print('Using configuration file %s' % conffile)
    config = VSXProxyServerConfig(conffile)
    start_logger(config.LOGFILE)

    logger('VSX Proxy Server Starting')


    server = VSXProxyServer(config)

    try:
        while True:
            asyncore.loop(timeout=2, count=1)
            # insert scheduling code here.
    except KeyboardInterrupt:
        print "Crtl+C pressed. Shutting down."
        logger('Shutting down from Ctrl+C')
        if LOGTOFILE:
            outfile.close()

        server.shutdown(socket.SHUT_RDWR)
        server.close()
        sys.exit()








