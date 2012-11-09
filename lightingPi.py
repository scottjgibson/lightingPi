from ConfigParser import SafeConfigParser
import argparse, socket, math
import getopt
import textwrap
import sys
from ola.ClientWrapper import ClientWrapper
from Adafruit_PWM_Servo_Driver import PWM

PIXEL_SIZE = 3
WHITE = [255,255,255] 
BLACK = [0,0,0] 
DMX_MAX = 255
PCA9685_MAX = 4095

class pca9685:
    def __init__(self, name):
        self.name = name
        self.type = 'pca9685_pwm'
        self.channel_config = []
        self.i2c_address = None
        self.dmx_channel_start = None
        self.dmx_channel_end = None
        self.handler = self.default_handler
        self.servo_min = 150  # Min pulse length out of 4096
        self.servo_max = 600  # Max pulse length out of 4096
        self.pwm = PWM(0x40, debug=True)
        self.pwm.setPWMFreq(60) # Set frequency to 60 Hz
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

    def default_handler(self,data):
        print self.name, " Using hardware handler"
        for channel_num, channel_type in enumerate(self.channel_config):
            if channel_type == 'Dimmer':
                if controller.verbose:
                    print "Channel:", channel_num, "Channel Type: ", channel_type,  "Value: ", data[self.dmx_channel_start+channel_num]
                self.pwm.setPWM(channel_num, 0, data[self.dmx_channel_start+channel_num]*PCA9685_MAX/DMX_MAX)
            if channel_type == 'Servo':
                if controller.verbose:
                    print "Channel:", channel_num, "Channel Type: ", channel_type,  "Value: ", data[self.dmx_channel_start+channel_num]
                # position is in 0-255 range
                # 0degree position = 150; max degree position = 450
                servo_max_delta = self.servo_max - self.servo_min
                value = self.servo_min  + data[self.dmx_channel_start+channel_num] * (servo_max_delta / DMX_MAX)
                if controller.verbose:
                    print "Channel:", channel_num, "Channel Type: ", channel_type,  "Value: ", value
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
        controller.send_spi(data)

    def rgb_pixel_chase_fill_handler(self, dmx_data):
        data  = []
        position = dmx_data[self.dmx_channel_start]*self.num_leds/255
        if controller.verbose:
            print position
        for i in range(position):
            data.append(WHITE)
        for i in range(self.num_leds - position):
            data.append(BLACK)
        controller.send_spi(data)
    
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
            result[j] = data[i][0]
            result[j+1] = data[i][1]
            result[j+2] = data[i][2]
            j = j + 3
        return result

    def send_spi(self, data):
        if controller.verbose:
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

class lightingPi:
    def __init__(self):
        self.dmx_universe = None
        self.fixture_list = []
        self.verbose = False

    def data_handler(self, dmx_data):
        data  = []
        for fixture in self.fixture_list:
            fixture.handler(dmx_data)


    def parseConfigFile(self, configFile):
        config = SafeConfigParser()
        config.read(configFile)
        self.dmx_universe = config.getint('general_config', 'dmx_universe')
        parsed_fixture_list = config.get('general_config', 'fixture_list').split(',')

        print "General Config: "
        print "Universe: ", self.dmx_universe
        for fixture_name in parsed_fixture_list:
            type = config.get(fixture_name, 'type')
            if type == 'rgb_pixel':
                new_fixture = RGB_Pixel_Fixture(fixture_name)
                new_fixture.mode = config.get(fixture_name, 'mode')
                new_fixture.spi_bus = config.get(fixture_name, 'spi_bus')
                new_fixture.chip_type =  config.get(fixture_name, 'chip_type')
                new_fixture.leds_per_channel = config.getint(fixture_name, 'leds_per_channel')
                new_fixture.num_leds = config.getint(fixture_name, 'num_leds')
                new_fixture.dmx_channel_start = config.getint(fixture_name, 'dmx_channel_start')
                new_fixture.dmx_channel_end = config.getint(fixture_name, 'dmx_channel_end')
                if new_fixture.mode == 'dimmer':
                    new_fixture.handler = new_fixture.rgb_pixel_handler
                if new_fixture.mode == 'chase':
                    new_fixture.handler = new_fixture.rgb_pixel_chase_handler
                if new_fixture.mode == 'chase':
                    new_fixture.handler = new_fixture.rgb_pixel_chase_fill_handler
                new_fixture.calculateGamma()
            if type == 'pca9685':
                new_fixture = pca9685(fixture_name)
                new_fixture.i2c_address = config.get(fixture_name, 'i2c_address')
                new_fixture.dmx_channel_start = config.getint(fixture_name, 'dmx_channel_start')
                new_fixture.dmx_channel_end = config.getint(fixture_name, 'dmx_channel_end')
                new_fixture.num_channels = config.getint(fixture_name, 'num_channels')
                for pca9685_channel in range(new_fixture.num_channels):
                    type = config.get(fixture_name, 'channel_%d'%pca9685_channel)
                    new_fixture.channel_config.append(type)

            self.fixture_list.append(new_fixture)
        print "Configured Fixtures: "
        for fixture in self.fixture_list:
            print fixture
    

    def run(self):
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
    args = parser.parse_args()
    controller.verbose = args.verbose

if __name__ == '__main__':
    controller = lightingPi()
    controller.parseConfigFile('config.ini')
    defineCliArguments(controller)
    controller.run()


