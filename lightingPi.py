from ConfigParser import SafeConfigParser
import argparse, socket, math
import getopt
import textwrap
import sys
from ola.ClientWrapper import ClientWrapper

PIXEL_SIZE = 3
WHITE = [255,255,255] 
BLACK = [0,0,0] 

class Fixture:
    def __init__(self, name):
        self.name = name
        self.type = None
        self.dmx_universe = None
        self.dmx_channel = None
        self.leds_per_channel = None
        self.num_leds = None
        self.num_channels = None
        self.data_handler = self.default_handler

    def __str__(self):
        ret = "\nName: %s\n" %  self.name 
        ret += "Type: %s\n" % self.type
        ret += "DMX Universe: %d\n" % self.dmx_universe 
        if (self.type == 'rgb_pixel'):
            ret += "DMX Channel (start): %d\n" % self.dmx_channel
            ret += "DMX Channel (end): %d\n" % (self.dmx_channel + self.num_channels)
            ret += "Num Leds: %d\n" % self.num_leds
            ret += "Leds per channel: %d\n" % self.leds_per_channel
        if (self.type == 'rgb_pixel_chase') or (self.type == 'rgb_pixel_chase_fill'):
            ret += "DMX Channel: %d\n" % self.dmx_channel
            ret += "Num Leds: %d\n" % self.num_leds
        if (self.type == 'pca9685'):
            ret += "DMX Channel (start): %d\n" % self.dmx_channel
            ret += "DMX Channel (end): %d\n" % (self.dmx_channel + self.num_channels)
        return ret

    def default_handler(self,data):
        print name, " Using default handler"
        print "Data:", data

    def rgb_pixel_handler(self, dmx_data):
        data  = []
        group_size = self.num_leds / self.num_channels
        for group in range(self.num_channels):
            for pixel in range(group_size):
                data.append(dmx_data[(group*PIXEL_SIZE):(group*PIXEL_SIZE)+PIXEL_SIZE])
        controller.send(data)

    def rgb_pixel_chase_handler(self, dmx_data):
        data  = []
        position = dmx_data[self.dmx_channel]*self.num_leds/255
        if controller.verbose:
            print position
        for i in range(position - 1):
            data.append(BLACK)
        data.append(WHITE)
        for i in range(self.num_leds - position):
            data.append(BLACK)
        controller.send(data)

    def rgb_pixel_chase_fill_handler(self, dmx_data):
        data  = []
        position = dmx_data[self.dmx_channel]*self.num_leds/255
        if controller.verbose:
            print position
        for i in range(position):
            data.append(WHITE)
        for i in range(self.num_leds - position):
            data.append(BLACK)
        controller.send(data)

class RaspberryDmx:
    def __init__(self):
        self.spidev = file('/dev/spidev0.0', "wb")
        self.gamma = bytearray(256)
        self.pixel_string_bus = None
        self.pixel_string_chip = None
        self.pixel_string_num_leds = None
        self.fixture_list = []
        self.verbose = False
        self.cmd = None
        self.num_leds = 50
        self.universe_list = []
        self.chip_type = 'WS2801'

    def getBytes(self, data):
        result = bytearray(len(data)* PIXEL_SIZE)
        j = 0
        for i in range(len(data)):
            result[j] = data[i][0]
            result[j+1] = data[i][1]
            result[j+2] = data[i][2]
            j = j + 3
        return result

    def send(self, data):
        if self.verbose:
            print "sending ",data
        bytedata = self.getBytes(data)
        pixels_in_buffer = len(data) / PIXEL_SIZE
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
            

    def parseConfigFile(self, configFile):
        config = SafeConfigParser()
        config.read(configFile)
        self.pca9685_i2c_addr = config.get('hw_config', 'pca9685_i2c_address')
        self.pixel_string_bus = config.get('hw_config', 'pixel_string_bus')
        self.pixel_string_chip =  config.get('hw_config', 'pixel_string_chip')
        self.pixel_string_num_leds  = config.getint('hw_config', 'pixel_string_num_leds')
        parsed_fixture_list = config.get('fixture_list', 'fixture_list').split(',')

        print "HW Config: "
        print "SPI bus:        ", self.pixel_string_bus
        print "Pixel Type:     ", self.pixel_string_chip
        print "Number of LEDs: ", self.pixel_string_num_leds
        for fixture_name in parsed_fixture_list:
            new_fixture = Fixture(fixture_name)
            new_fixture.type = config.get(new_fixture.name, 'type')
            new_fixture.dmx_universe = config.getint(new_fixture.name, 'dmx_universe')
            new_fixture.dmx_channel = config.getint(new_fixture.name, 'dmx_channel')
            if new_fixture.type == 'rgb_pixel':
                new_fixture.leds_per_channel = config.getint(new_fixture.name, 'leds_per_channel')
                new_fixture.num_leds = config.getint(new_fixture.name, 'num_leds')
                new_fixture.data_handler = new_fixture.rgb_pixel_handler
                new_fixture.num_channels = new_fixture.dmx_channel + new_fixture.num_leds/new_fixture.leds_per_channel
            if new_fixture.type == 'rgb_pixel_chase':
                new_fixture.num_leds = config.getint(new_fixture.name, 'num_leds')
                new_fixture.data_handler = new_fixture.rgb_pixel_chase_handler
                new_fixture.num_channels = 1
            if new_fixture.type == 'rgb_pixel_chase_fill':
                new_fixture.num_leds = config.getint(new_fixture.name, 'num_leds')
                new_fixture.data_handler = new_fixture.rgb_pixel_chase_fill_handler
                new_fixture.num_channels = 1
            if new_fixture.type == 'pca9685':
                new_fixture.num_channels = config.getint(new_fixture.name, 'num_channels')
                #new_fixture.data_handler = new_fixture.rgb_pixel_chase_fill_handler
            #check if this universe already exists for a fixture
            new_universe = True
            if not any(universe == new_fixture.dmx_universe for universe in self.universe_list):
                self.universe_list.append(new_fixture.dmx_universe)
            self.fixture_list.append(new_fixture)
        print "Configured Fixtures: "
        for fixture in self.fixture_list:
            print fixture
    
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
    

    def run(self):
        wrapper = ClientWrapper()
        client = wrapper.Client()
        for fixture in self.fixture_list:
            client.RegisterUniverse(fixture.dmx_universe, client.REGISTER, fixture.data_handler)
        wrapper.Run()

# ==================================================================================================
# ====================      Argument parsing          ==========================================
# ==================================================================================================

def defineCliArguments(controller):
    parser = argparse.ArgumentParser(add_help=True,version='1.0', prog='pixelpi.py')
    parser.add_argument('--verbose', action='store_true', dest='verbose', default=False, help='enable verbose mode')
    args = parser.parse_args()
    controller.verbose = args.verbose

if __name__ == '__main__':
    controller = RaspberryDmx()
    controller.parseConfigFile('config.ini')
    controller.calculateGamma()
    defineCliArguments(controller)
    controller.run()


