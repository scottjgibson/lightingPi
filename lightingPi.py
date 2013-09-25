from ConfigParser import SafeConfigParser
import argparse, socket, math
import getopt
import textwrap
import sys
import liblo
from ola.ClientWrapper import ClientWrapper
from Adafruit_PWM_Servo_Driver import PWM

PIXEL_SIZE = 3
WHITE = [255,255,255] 
BLACK = [0,0,0] 
DMX_MAX = 255
PCA9685_MAX = 4095

def fallback(path, args, types, src):
    print "got unknown message '%s' from '%s'" % (path, src.get_url())
    for a, t in zip(args, types):
        print "argument of type '%s': %s" % (t, a)

class OscMap:
    def __init__(self, name):
        self.name = name
        self.osc_path = None
        self.format = None
        self.mapping = None
    def __str__(self):
        ret = "\nName: %s\n" %  self.name 
        ret += "Path: %s\n" % self.osc_path
        ret += "Format String: %s\n" % self.format
        ret += "DMX Channel Mapping: %s\n" % self.mapping
        return ret

class pca9685:
    def __init__(self, name):
        self.name = name
        self.type = 'pca9685_pwm'
        self.channel_config = []
        self.i2c_address = None
        self.dmx_channel_start = None
        self.dmx_channel_end = None
        self.handler = self.default_handler
        self.servo_min = 103  # Min pulse length out of 4096 = 20mS
        self.servo_max = 500  # Max pulse length out of 4096
        self.pwm = PWM(0x40, debug=True)
        self.pwm.setPWMFreq(50) # Set frequency to 60 Hz
    def __str__(self):
        ret = "\nName: %s\n" %  self.name 
        ret += "Type: %s\n" % self.type
        ret += "I2C Address: %s\n" % self.i2c_address
        ret += "DMX Channel Start: %d\n" % self.dmx_channel_start
        ret += "DMX Channel End: %d\n" % self.dmx_channel_end
        for channel_num, channel_type in enumerate(self.channel_config):
            ret += "PWM Channel %d: " % channel_num
            ret += "Type: %s \n" % channel_type
        return ret

    def default_handler(self,data):
        print name, " Using default handler"
        print "Data:", data

    def pca9685_handler(self,data):
        if controller.verbose:
            print self.name, " Using hardware handler"
        for channel_num, channel_type in enumerate(self.channel_config):
            if channel_type == 'Dimmer':
                if controller.verbose:
                    print "Channel:", channel_num, "Channel Type: ", channel_type,  "Value: ", data[self.dmx_channel_start+channel_num]
                self.pwm.setPWM(channel_num, 0, data[self.dmx_channel_start+channel_num]*PCA9685_MAX/DMX_MAX)
            elif channel_type == 'Servo':
                if controller.verbose:
                    print "Channel:", channel_num, "Channel Type: ", channel_type,  "Value: ", data[self.dmx_channel_start+channel_num]
                # position is in 0-255 range
                # 0degree position = 150; max degree position = 450
                servo_max_delta = self.servo_max - self.servo_min
                value = self.servo_min  + data[self.dmx_channel_start+channel_num] * (servo_max_delta / DMX_MAX)
                self.pwm.setPWM(channel_num, 0, value)

class RGB_Pixel_Fixture:
    def __init__(self, name):
        self.name = name
        self.mode = None
        self.type ='rgb_pixel'
        self.spidev = file('/dev/spidev0.0', "wb")
        self.dmx_channel_start = None
        self.dmx_channel_end = None
        self.leds_per_channel = None
        self.num_leds = None
        self.handler = self.default_handler
        self.gamma = bytearray(256)
        self.chip_type = None

    def __str__(self):
        ret = "\nName: %s\n" %  self.name 
        ret += "Type: %s\n" % self.type
        ret += "Mode: %s\n" % self.mode
        ret += "DMX Channel (start): %d\n" % self.dmx_channel_start
        ret += "DMX Channel (end): %d\n" % self.dmx_channel_end
        ret += "Num Leds: %d\n" % self.num_leds
        if self.type == 'rbg_pixel':
            ret += "Leds per channel: %d\n" % self.leds_per_channel
        return ret

    def default_handler(self,data):
        print self.name, " Using default handler"
        print "Data:", data

    def rgb_pixel_handler(self, dmx_data):
        data  = []
        num_channels = self.dmx_channel_end - self.dmx_channel_start
        group_size = self.num_leds / num_channels
        for group in range(num_channels):
            for pixel in range(group_size):
                data.append(dmx_data[(group*PIXEL_SIZE):(group*PIXEL_SIZE)+PIXEL_SIZE])
        self.send_spi(data)

    def rgb_pixel_chase_handler(self, dmx_data):
        data  = []
        position = dmx_data[self.dmx_channel_start]*self.num_leds/255
        if controller.verbose:
            print position
        for i in range(position - 1):
            data.append(BLACK)
        data.append(WHITE)
        for i in range(self.num_leds - position):
            data.append(BLACK)
        self.send_spi(data)

    def rgb_pixel_chase_fill_handler(self, dmx_data):
        data  = []
        position = dmx_data[self.dmx_channel_start]*self.num_leds/255
        if controller.verbose:
            print position
        for i in range(position):
            data.append(WHITE)
        for i in range(self.num_leds - position):
            data.append(BLACK)
        self.send_spi(data)
    
    def calculateGamma(self):
        # Calculate gamma correction table. This includes
        # LPD8806-specific conversion (7-bit color w/high bit set).
        if self.chip_type == "LPD8806":
            for i in range(256):
                self.gamma[i] = 0x80 | int(pow(float(i) / 255.0, 2.5) * 127.0 + 0.5)
        if self.chip_type == "WS2801":
            for i in range(256):
                self.gamma[i] = int(pow(float(i) / 255.0, 2.5) * 255.0 )
        # LPD6803 has 5 bit color, this seems to work but is not exact.
        if self.chip_type == "LPD6803":
            for i in range(256):
                self.gamma[i] = int(pow(float(i) / 255.0, 2.0) * 255.0 + 0.5)

    # Apply Gamma Correction and RGB / GRB reordering
    # Optionally perform brightness adjustment
    def filter_pixel(self, input_pixel, brightness):
        input_pixel[0] = int(brightness * input_pixel[0])
        input_pixel[1] = int(brightness * input_pixel[1])
        input_pixel[2] = int(brightness * input_pixel[2])
        output_pixel = bytearray(PIXEL_SIZE)
        if self.chip_type == "LPD8806":
            # Convert RGB into GRB bytearray list.
            output_pixel[0] = self.gamma[input_pixel[1]]
            output_pixel[1] = self.gamma[input_pixel[0]]
            output_pixel[2] = self.gamma[input_pixel[2]]
        else:
            output_pixel[0] = self.gamma[input_pixel[0]]
            output_pixel[1] = self.gamma[input_pixel[1]]
            output_pixel[2] = self.gamma[input_pixel[2]]
        return output_pixel
            

    def getBytes(self, data):
        result = bytearray(len(data)* PIXEL_SIZE)
        j = 0
        for i in range(len(data)):
            #result[j] = data[i][0]
            #result[j+1] = data[i][1]
            #result[j+2] = data[i][2]
            for k in range(3):
               try:
                  result[j+k] = data[i][k]
               except IndexError:
                  result[j+k] = 0

            j = j + 3
        return result

    def send_spi(self, data):
        if controller.verbose:
            print "sending ",data
        bytedata = self.getBytes(data)
        pixels_in_buffer = len(data)
        pixels = bytearray(pixels_in_buffer * PIXEL_SIZE)
        for pixel_index in range(pixels_in_buffer):
            pixel_to_adjust = bytearray(bytedata[(pixel_index * PIXEL_SIZE):((pixel_index * PIXEL_SIZE) + PIXEL_SIZE)])
            pixels[((pixel_index)*PIXEL_SIZE):] = self.filter_pixel(pixel_to_adjust[:], 1)
        if self.chip_type == "LPD6803":
            pixel_out_bytes = bytearray(2)
            spidev.write(bytearray(b'\x00\x00'))
            pixel_count = len(pixels) / PIXEL_SIZE
            for pixel_index in range(pixel_count):
                pixel_in = bytearray(pixels[(pixel_index * PIXEL_SIZE):((pixel_index * PIXEL_SIZE) + PIXEL_SIZE)])
                pixel_out = 0b1000000000000000 # bit 16 must be ON
                pixel_out |= (pixel_in[0] & 0x00F8) << 7 # RED is bits 11-15
                pixel_out |= (pixel_in[1] & 0x00F8) << 2 # GREEN is bits 6-10
                pixel_out |= (pixel_in[2] & 0x00F8) >> 3 # BLUE is bits 1-5
            pixel_out_bytes[0] = (pixel_out & 0xFF00) >> 8
            pixel_out_bytes[1] = (pixel_out & 0x00FF) >> 0
            self.spidev.write(pixel_out_bytes)
        else:
            self.spidev.write(pixels)
        self.spidev.flush()

class LightingPi:
    def __init__(self):
        self.osc_server = None
        self.osc = None
        self.osc_buffer = bytearray(512)
        self.raw = None
        self.dmx_universe = None
        self.fixture_list = []
        self.osc_map_list = []
        self.verbose = False

    def data_handler(self, dmx_data):
        for fixture in self.fixture_list:
            fixture.handler(dmx_data)

    def osc_callback(self, path, args, types, src, map_name):
        if controller.verbose:
            print "got new message '%s' from '%s'" % (path, src.get_url())
        for a, t in zip(args, types):
            if controller.verbose:
                print "argument of type '%s': %s - User Data: %s" % (t, a, map_name)

        #look up the corresponding osc_map based on the map_name
        for osc_map in self.osc_map_list:
            if osc_map.name == map_name:
                for fixture in self.fixture_list:
                    if set(range(fixture.dmx_channel_start, fixture.dmx_channel_end)).issuperset(set(osc_map.mapping)):
                        for i, channel in enumerate(osc_map.mapping):
                            if controller.verbose:
                                print "setting channel: %d: Value: %d" % (channel, int(args[i]))
                            if(osc_map.format[i] == 'f'):
                                self.osc_buffer[channel] = int(args[i])
                            else:
                                self.osc_buffer[channel] = args[i]
                        fixture.handler(self.osc_buffer)

    def register_osc_callbacks(self):
         for osc_map in self.osc_map_list:
            if controller.verbose:
                print "registering handler for path: %s with format: %s" %(osc_map.osc_path, osc_map.format)
            self.osc_server.add_method(osc_map.osc_path, osc_map.format, self.osc_callback, osc_map.name)
         self.osc_server.add_method(None, None, fallback)
            
    def parseConfigFile(self, configFile):
        config = SafeConfigParser()
        config.read(configFile)
        self.dmx_universe = config.getint('general_config', 'dmx_universe')
        parsed_fixture_list = config.get('general_config', 'fixture_list').split(',')
        parsed_osc_map_list = config.get('general_config', 'osc_map_list').split(',')

        print "General Config: "
        print "Universe: ", self.dmx_universe
        for fixture_name in parsed_fixture_list:
            type = config.get(fixture_name, 'type')
            if type == 'rgb_pixel':
                new_fixture = RGB_Pixel_Fixture(fixture_name)
                new_fixture.mode = config.get(fixture_name, 'mode')
                new_fixture.spi_bus = config.get(fixture_name, 'spi_bus')
                new_fixture.chip_type =  config.get(fixture_name, 'chip_type')
                new_fixture.num_leds = config.getint(fixture_name, 'num_leds')
                new_fixture.dmx_channel_start = config.getint(fixture_name, 'dmx_channel_start')
                new_fixture.dmx_channel_end = config.getint(fixture_name, 'dmx_channel_end')
                if new_fixture.mode == 'dimmer':
                    new_fixture.leds_per_channel = config.getint(fixture_name, 'leds_per_channel')
                    new_fixture.handler = new_fixture.rgb_pixel_handler
                if new_fixture.mode == 'chase':
                    new_fixture.handler = new_fixture.rgb_pixel_chase_handler
                if new_fixture.mode == 'chase_fill':
                    new_fixture.handler = new_fixture.rgb_pixel_chase_fill_handler
                new_fixture.calculateGamma()
                self.fixture_list.append(new_fixture)
            if type == 'pca9685':
                new_fixture = pca9685(fixture_name)
                new_fixture.i2c_address = config.get(fixture_name, 'i2c_address')
                new_fixture.dmx_channel_start = config.getint(fixture_name, 'dmx_channel_start')
                new_fixture.dmx_channel_end = config.getint(fixture_name, 'dmx_channel_end')
                new_fixture.num_channels = config.getint(fixture_name, 'num_channels')
                new_fixture.handler = new_fixture.pca9685_handler
                for pca9685_channel in range(new_fixture.num_channels):
                    type = config.get(fixture_name, 'channel_%d'%pca9685_channel)
                    new_fixture.channel_config.append(type)
                self.fixture_list.append(new_fixture)
        print "Configured Fixtures: "
        for fixture in self.fixture_list:
            print fixture

        for map_name in parsed_osc_map_list:
            type = config.get(map_name, 'type')
            if type == 'osc_map':
                new_map = OscMap(map_name)
                new_map.osc_path = config.get(map_name, 'path')
                new_map.format = config.get(map_name, 'format')
                new_map.mapping = map(int, config.get(map_name, 'mapping').split(","))
                self.osc_map_list.append(new_map)
        for osc_map in self.osc_map_list:
            print osc_map

    def run(self):
        if self.raw:
            print ("Start Raw listener " + self.raw_ip + ":" + str(self.port))
            sock = socket.socket( socket.AF_INET, # Internet
                          socket.SOCK_DGRAM ) # UDP
            sock.bind( (self.raw_ip,self.port) )
            UDP_BUFFER_SIZE = 4096
            while True:
                data, addr = sock.recvfrom( UDP_BUFFER_SIZE ) # blocking call
                self.data_handler(data)

        elif self.osc:
            try:
                self.osc_server = liblo.Server(self.port)
            except liblo.ServerError, err:
                print str(err)
                sys.exit()

            controller.register_osc_callbacks();

            # loop and dispatch messages every 100ms
            while True:
                self.osc_server.recv(100)
     
        else:
            wrapper = ClientWrapper()
            client = wrapper.Client()
            client.RegisterUniverse(self.dmx_universe, client.REGISTER, self.data_handler)
            wrapper.Run()

# ==================================================================================================
# ====================      Argument parsing          ==========================================
# ==================================================================================================

def defineCliArguments(controller):
    parser = argparse.ArgumentParser(add_help=True,version='1.0', prog='pixelpi.py')
    parser.add_argument('--verbose', action='store_true', dest='verbose', default=False, help='enable verbose mode')
    parser.add_argument('--port', action='store', dest='port', required=False, default=6803, type=int, help='Port to receive raw channel data (does not use OLA')
    parser.add_argument('--raw', action='store_true', dest='raw', default=False, help='enable raw mode')
    parser.add_argument('--osc', action='store_true', dest='osc', default=False, help='enable osc mode')
    parser.add_argument('--raw-ip', action='store', dest='ip', required=False, default='127.0.0.1', help='Used for raw mode, listening address')

    args = parser.parse_args()
    controller.verbose = args.verbose
    controller.raw = args.raw
    controller.osc = args.osc
    controller.port = args.port
    controller.raw_ip = args.ip


if __name__ == '__main__':
    controller = LightingPi()
    controller.parseConfigFile('config.ini')
    defineCliArguments(controller)
    controller.run()


