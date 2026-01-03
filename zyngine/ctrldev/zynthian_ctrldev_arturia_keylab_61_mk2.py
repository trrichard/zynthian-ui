#!/usr/bin/python3
# -*- coding: utf-8 -*-
# ******************************************************************************
# ZYNTHIAN PROJECT: Zynthian Control Device Driver
#
# Zynthian Control Device Driver for "Akai APC Key 25 mk2"
#
# Copyright (C) 2023-2025 Oscar Aceña <oscaracena@gmail.com>
#
# ******************************************************************************
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License as
# published by the Free Software Foundation; either version 2 of
# the License, or any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# For a full copy of the GNU General Public License see the LICENSE.txt file.
#
# ******************************************************************************

import jack
import time
import signal
import logging
from bisect import bisect
from copy import deepcopy
import multiprocessing as mp
from functools import partial
from threading import Thread, RLock, Event

from zynlibs.zynseq import zynseq
from zyncoder.zyncore import lib_zyncore
from zyngine.zynthian_signal_manager import zynsigman
from zyngine.zynthian_engine_audioplayer import zynthian_engine_audioplayer

from zyngine.ctrldev.zynthian_ctrldev_base import zynthian_ctrldev_zynmixer, zynthian_ctrldev_zynpad
from zyngine.ctrldev.zynthian_ctrldev_base_extended import RunTimer, KnobSpeedControl, ButtonTimer, CONST
from zyngine.ctrldev.zynthian_ctrldev_base_ui import ModeHandlerBase
from zyngine.ctrldev.zynthian_ctrldev_base import zynthian_ctrldev_base



# FIXME: these defines should be taken from where they are defined (zynseq.h)
MAX_STUTTER_COUNT = 32
MAX_STUTTER_DURATION = 96

# MIDI channel events (first 4 bits), next 4 bits is the channel!
EV_NOTE_ON = 0x09
EV_NOTE_OFF = 0x08
EV_CC = 0x0B

# MIDI system events (first 8 bits)
EV_SYSEX = 0xF0
EV_CLOCK = 0xF8
EV_CONTINUE = 0xFB

# APC Key25 buttons
BTN_SHIFT = 0x62
BTN_STOP_ALL_CLIPS = 0x51
BTN_PLAY = 0x5E
BTN_RECORD = 0x5F

BTN_TRACK_1 = BTN_UP = 0x40
BTN_TRACK_2 = BTN_DOWN = 0x41
BTN_TRACK_3 = BTN_LEFT = 0x42
BTN_TRACK_4 = BTN_RIGHT = 0x43
BTN_TRACK_5 = BTN_KNOB_CTRL_VOLUME = 0x44
BTN_TRACK_6 = BTN_KNOB_CTRL_PAN = 0x45
BTN_TRACK_7 = BTN_KNOB_CTRL_SEND = 0x46
BTN_TRACK_8 = BTN_KNOB_CTRL_DEVICE = 0x47

BTN_SOFT_KEY_START = 0x52
BTN_SOFT_KEY_END = 0x56
BTN_SOFT_KEY_CLIP_STOP = BTN_KNOB_1 = 0x52
BTN_SOFT_KEY_SOLO = BTN_KNOB_2 = 0x53
BTN_SOFT_KEY_MUTE = BTN_KNOB_3 = 0x54
BTN_SOFT_KEY_REC_ARM = BTN_KNOB_4 = 0x55
BTN_SOFT_KEY_SELECT = 0x56

BTN_PAD_START = 0x00
BTN_PAD_END = 0x27
BTN_PAD_29 = BTN_ALT = 0x1C
BTN_PAD_30 = BTN_METRONOME = 0x1D
BTN_PAD_31 = BTN_PAD_STEP = 0x1E
BTN_PAD_37 = BTN_OPT_ADMIN = 0x24
BTN_PAD_38 = BTN_MIX_LEVEL = 0x25
BTN_PAD_39 = BTN_CTRL_PRESET = 0x26
BTN_PAD_40 = BTN_ZS3_SHOT = 0x27
BTN_PAD_5 = BTN_PAD_LEFT = 0x04
BTN_PAD_6 = BTN_PAD_DOWN = 0x05
BTN_PAD_7 = BTN_PAD_RIGHT = 0x06
BTN_PAD_8 = BTN_F4 = 0x07
BTN_PAD_13 = BTN_BACK_NO = 0x0C
BTN_PAD_14 = BTN_PAD_UP = 0x0D
BTN_PAD_15 = BTN_SEL_YES = 0x0E
BTN_PAD_16 = BTN_F3 = 0x0F
BTN_PAD_21 = BTN_PAD_RECORD = 0x14
BTN_PAD_23 = BTN_PAD_PLAY = 0x16
BTN_PAD_24 = BTN_F2 = 0x17
BTN_PAD_32 = BTN_F1 = 0x1F

# APC Key25 knobs
KNOB_1 = KNOB_LAYER = 0x30
KNOB_2 = KNOB_SNAPSHOT = 0x31
KNOB_3 = 0x32
KNOB_4 = 0x33
KNOB_5 = KNOB_BACK = 0x34
KNOB_6 = KNOB_SELECT = 0x35
KNOB_7 = 0x36
KNOB_8 = 0x37

# APC Key25 MK2 LED colors and modes
class COLORS:
    COLOR_BLACK = 0x00
    COLOR_DARK_GREY = 0x01
    COLOR_RED = 0x05
    COLOR_GREEN = COLOR_STATE_1 = 0x15
    COLOR_BLUE = COLOR_STATE_0 = 0x25
    COLOR_AQUA = 0x21
    COLOR_BLUE_DARK = COLOR_ALT_OFF = 0x2D
    COLOR_BLUE_LIGHT = 0x24
    COLOR_WHITE = COLOR_FN = 0x03
    COLOR_EGYPT = 0x6C
    COLOR_ORANGE = COLOR_STATE_2 = 0x09
    COLOR_ORANGE_LIGHT = 0x08
    COLOR_AMBER = 0x54
    COLOR_RUSSET = 0x3D
    COLOR_PURPLE = COLOR_ALT_ON = 0x51
    COLOR_PINK = 0x39
    COLOR_PINK_LIGHT = 0x52
    COLOR_PINK_WARM = 0x38
    COLOR_YELLOW = 0x0D
    COLOR_LIME = COLOR_PLAYING = 0x4B
    COLOR_LIME_DARK = 0x11
    COLOR_DARK_GREEN = 0x41
    COLOR_GREEN_YELLOW = 0x4A
    COLOR_BROWNISH_RED = 0x0A
    COLOR_BROWN_LIGHT = 0x7E
    SOFT_OFF = 0x00
    SOFT_ON = 0x01
    SOFT_BLINK = 0x02

# mk2: midi channel,
# mk1: midi channel stays 0, always on, blink is color + 1
    # 0=off,
    # 1=green,
    # 2=green blink,
    # 3=red,
    # 4=red blink,
    # 5=yellow,
    # 6=yellow blink,
    # 7-127=green

LED_BRIGHT_10 = 0x00
LED_BRIGHT_25 = 0x01
LED_BRIGHT_50 = 0x02
LED_BRIGHT_65 = 0x03
LED_BRIGHT_75 = 0x04
LED_BRIGHT_90 = 0x05
LED_BRIGHT_100 = 0x06
LED_PULSING_16 = 0x07
LED_PULSING_8 = 0x08
LED_PULSING_4 = 0x09
LED_PULSING_2 = 0x0A
LED_BLINKING_24 = 0x0B
LED_BLINKING_16 = 0x0C
LED_BLINKING_8 = 0x0D
LED_BLINKING_4 = 0x0E
LED_BLINKING_2 = 0x0F

# Function/State constants
FN_VOLUME = 0x01
FN_PAN = 0x02
FN_SOLO = 0x03
FN_MUTE = 0x04
FN_REC_ARM = 0x05
FN_SELECT = 0x06
FN_SCENE = 0x07
FN_SEQUENCE_MANAGER = 0x08
FN_COPY_SEQUENCE = 0x09
FN_MOVE_SEQUENCE = 0x0A
FN_CLEAR_SEQUENCE = 0x0B
FN_PLAY_NOTE = 0x0C
FN_REMOVE_NOTE = 0x0D
FN_REMOVE_PATTERN = 0x0F
FN_SELECT_PATTERN = 0x10
FN_CLEAR_PATTERN = 0x11


# --------------------------------------------------------------------------
# 'Akai APC Key 25 mk2' device controller class
# --------------------------------------------------------------------------
class zynthian_ctrldev_arturia_keylab_61_mk2(zynthian_ctrldev_base):#zynthian_ctrldev_zynmixer, zynthian_ctrldev_zynpad):

    dev_ids = ["KeyLab mkII 61 IN 1"]
    multi_device_ids = {"KeyLab mkII 61 2": True}
    driver_name = 'Arturia Keylab 61 Mk2'
    driver_description = 'Full UI integration'
    # Unroute 9: pads 
    unroute_from_chains = 0b0000001000000000 # allow most channels. 

    COLOR_SET = COLORS

    def __init__(self, state_manager, idev_in, idev_out=None):
        logging.info("initializing {} with port in:{} out:{}".format(self.driver_name, idev_in, idev_out))
      
        # NOTE: init will call refresh(), so _current_hanlder must be ready!
        super().__init__(state_manager, idev_in, idev_out)

    def init(self):
        super().init()
    

    def end(self):
        super().end()
        #zynthian_ctrldev_zynpad.end(self)

    def refresh(self):
        # PadMatrix is handled in volume/pan modes (when mixer handler is active)
        logging.info("refresh")
        # super.refresh()
       
    def midi_event(self, ev, idev=None):
        """ get a midi event  and do something with it. """
        logging.info("event {} on {}".format(ev, idev))
        evtype = (ev[0] >> 4) & 0x0F
        evchan = ev[0] & 0x0F
        # Note ON
        if evtype == 0x9:
            note = ev[1] & 0x7F
            vel = ev[2] & 0x7F
            logging.info("event ON ev: {} note:{} vel:{} channel: {}".format(ev, note, vel, evchan))
            #logging.debug(f"Chan {evchan}, Note ON {note}")
            PLAYING_COLOR = 21
            if vel > 0:
                lib_zyncore.dev_send_note_on(self.idev_out, 0, note, vel)
            else:
                lib_zyncore.dev_send_note_on(self.idev_out, 0, note, 0)
            return True
        # Note OFF
        elif evtype == 0x8:
            note = ev[1] & 0x7F
            #logging.debug(f"Chan {evchan}, Note OFF {note}")
            lib_zyncore.dev_send_note_on(self.idev_out, 0, note, 0)
            return True
        return False


#    def update_mixer_strip(self, chan, symbol, value):
#        """Update hardware indicators for a mixer strip: mute, solo, level, balance, etc.
#        *SHOULD* be implemented by child class
#
#        chan - Mixer strip index
#        symbol - Control name
#        value - Control value
#        """
#        pass
#
#    def update_mixer_active_chain(self, active_chain):
#        """Update hardware indicators for active_chain
#        *SHOULD* be implemented by child class
#
#        active_chain - Active chain
#        """
#        pass
#
#    def update_seq_state(self, bank, seq, state=None, mode=None, group=None):
#        """Update hardware indicators for a sequence (pad): playing state etc.
#        *SHOULD* be implemented by child class
#
#        bank - bank
#        seq - sequence index
#        state - sequence's state
#        mode - sequence's mode
#        group - sequence's group
#        """
#        pass
#
#    def pad_off(self, col, row):
#        """Light-Off the pad specified with column & row
#        *SHOULD* be implemented by child class
#        """
#        pass
#
#
#  