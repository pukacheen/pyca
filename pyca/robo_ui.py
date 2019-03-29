# Copyright 2017 the pycolab Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Frontends for humans who want to play pycolab games."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import copy
import curses
import datetime
from enum import Enum

from pycolab.protocols import logging as plab_logging
from pycolab import human_ui
import six


class Mode(Enum):
    Autonomous = 1      # robot moves by itself
    Demonstration = 2   # demonstration mode: robot does not move
    Participation = 3   # participation mode: robot moves with player'

class CursesUi(human_ui.CursesUi):
    """A terminal-based UI for pycolab games."""

    def __init__(self, keys_to_actions,
                 delay=None, repainter=None, colour_fg=None, colour_bg=None, croppers=None,
                 agent=None):
        super().__init__(keys_to_actions, delay, repainter, colour_fg, colour_bg, croppers)
        self.robo = agent
        self.mode = Mode.Autonomous

    def switch_mode(self):
        if self.mode == Mode.Autonomous:
            self.mode = Mode.Demonstration
        else:
            self.mode = Mode.Autonomous

    def _display(self, screen, observations, score, elapsed):
        """Redraw the game board onto the screen, with elapsed time and score.

        Args:
          screen: the main, full-screen curses window.
          observations: a list of `rendering.Observation` objects containing
              subwindows of the current game board.
          score: the total return earned by the player, up until now.
          elapsed: a `datetime.timedelta` with the total time the player has spent
              playing this game.
        """
        screen.erase()  # Clear the screen

        # Display the game clock and the current score.
        screen.addstr(0, 2, human_ui._format_timedelta(elapsed), curses.color_pair(0))
        screen.addstr(0, 20, 'Score: {}'.format(score), curses.color_pair(0))
        screen.addstr(0, 40, 'Mode: {}'.format(self.mode.name), curses.color_pair(0))

        # Display cropped observations side-by-side.
        leftmost_column = 0
        for observation in observations:
            # Display game board rows one-by-one.
            for row, board_line in enumerate(observation.board, start=1):
                screen.move(row, leftmost_column)  # Move to start of this board row.
                # Display game board characters one-by-one. We iterate over them as
                # integer ASCII codepoints for easiest compatibility with python2/3.
                for codepoint in six.iterbytes(board_line.tostring()):
                    screen.addch(
                        codepoint, curses.color_pair(self._colour_pair[codepoint]))

            # Advance the leftmost column for the next observation.
            leftmost_column += observation.board.shape[1] + 3

        # Redraw the game screen (but in the curses memory buffer only).
        screen.noutrefresh()

    def _init_curses_and_play(self, screen):
        """Overrides the original by using keyword_to_action method

        Set up an already-running curses; do interaction loop.

        This method is intended to be passed as an argument to `curses.wrapper`,
        so its only argument is the main, full-screen curses window.

        Args:
          screen: the main, full-screen curses window.

        Raises:
          ValueError: if any key in the `keys_to_actions` dict supplied to the
              constructor has already been reserved for use by `CursesUi`.
        """
        # See whether the user is using any reserved keys. This check ought to be in
        # the constructor, but it can't run until curses is actually initialised, so
        # it's here instead.
        for key, action in six.iteritems(self._keycodes_to_actions):
            if key in (curses.KEY_PPAGE, curses.KEY_NPAGE):
                raise ValueError(
                    'the keys_to_actions argument to the CursesUi constructor binds '
                    'action {} to the {} key, which is reserved for CursesUi. Please '
                    'choose a different key for this action.'.format(
                        repr(action), repr(curses.keyname(key))))

        # If the terminal supports colour, program the colours into curses as
        # "colour pairs". Update our dict mapping characters to colour pairs.
        self._init_colour()
        curses.curs_set(0)  # We don't need to see the cursor.
        if self._delay is None:
            screen.timeout(-1)  # Blocking reads
        else:
            screen.timeout(self._delay)  # Nonblocking (if 0) or timing-out reads

        # Create the curses window for the log display
        rows, cols = screen.getmaxyx()
        console = curses.newwin(rows // 2, cols, rows - (rows // 2), 0)

        # By default, the log display window is hidden
        paint_console = False

        def crop_and_repaint(observation):
            # Helper for game display: applies all croppers to the observation, then
            # repaints the cropped subwindows. Since the same repainter is used for
            # all subwindows, and since repainters "own" what they return and are
            # allowed to overwrite it, we copy repainted observations when we have
            # multiple subwindows.
            observations = [cropper.crop(observation) for cropper in self._croppers]
            if self._repainter:
                if len(observations) == 1:
                    return [self._repainter(observations[0])]
                else:
                    return [copy.deepcopy(self._repainter(obs)) for obs in observations]
            else:
                return observations

        # Kick off the game---get first observation, crop and repaint as needed,
        # initialise our total return, and display the first frame.
        observation, reward, _ = self._game.its_showtime()
        observations = crop_and_repaint(observation)
        self._total_return = reward
        self._display(
            screen, observations, self._total_return, elapsed=datetime.timedelta())

        # Oh boy, play the game!
        while not self._game.game_over:
            # Wait (or not, depending) for user input, and convert it to an action.
            # Unrecognised keycodes cause the game display to repaint (updating the
            # elapsed time clock and potentially showing/hiding/updating the log
            # message display) but don't trigger a call to the game engine's play()
            # method. Note that the timeout "keycode" -1 is treated the same as any
            # other keycode here.
            keycode = screen.getch()
            if keycode == curses.KEY_PPAGE:  # Page Up? Show the game console.
                paint_console = True
            elif keycode == curses.KEY_NPAGE:  # Page Down? Hide the game console.
                paint_console = False
            elif keycode == 127:
                # backspace switch modes
                self.switch_mode()
            elif keycode == -1 and self.mode in (Mode.Autonomous,):
                # If the user did nothing, Robo gets to move!
                action = self.robo.decide()
                observation, reward, _ = self._game.play(action)
                observations = crop_and_repaint(observation)
                if self._total_return is None:
                    self._total_return = reward
                elif reward is not None:
                    self._total_return += reward
            elif keycode in self._keycodes_to_actions:
                # when the user presses a key, we exit from autonomous to participation
                if self.mode == Mode.Autonomous:
                    self.mode = Mode.Participation
                # Convert the keycode to a game action and send that to the engine.
                # Receive a new observation, reward, discount; crop and repaint; update
                # total return.
                action = self._keycodes_to_actions[keycode]
                observation, reward, _ = self._game.play(action)
                observations = crop_and_repaint(observation)
                if self._total_return is None:
                    self._total_return = reward
                elif reward is not None:
                    self._total_return += reward

            # Update the game display, regardless of whether we've called the game's
            # play() method.
            elapsed = datetime.datetime.now() - self._start_time
            self._display(screen, observations, self._total_return, elapsed)

            # Update game console message buffer with new messages from the game.
            self._update_game_console(
                plab_logging.consume(self._game.the_plot), console, paint_console)

            # Show the screen to the user.
            curses.doupdate()