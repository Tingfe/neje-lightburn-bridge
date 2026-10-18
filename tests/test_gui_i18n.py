"""Offline locale checks; these tests never open Tk, BLE or a machine socket."""

import ast
from pathlib import Path
import sys
import tempfile
import unittest
from string import Formatter
from unittest.mock import Mock, patch

TOOLS = Path(__file__).resolve().parents[1] / 'tools'
sys.path.insert(0, str(TOOLS))
import desktop_settings
import device_setup
import neje_app
import neje_i18n


class BridgeLanguageTests(unittest.TestCase):
    def test_every_chinese_operator_message_has_an_english_translation(self):
        tree = ast.parse((TOOLS / 'neje_app.py').read_text())
        messages = set()
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in ('_', '_t') and node.args):
                messages.update(child.value for child in ast.walk(node.args[0])
                                if isinstance(child, ast.Constant) and isinstance(child.value, str)
                                and any('\u4e00' <= char <= '\u9fff' for char in child.value))
        self.assertFalse(messages - neje_i18n.EN.keys(), messages - neje_i18n.EN.keys())
        self.assertIn('Machine', neje_i18n.translate('本次停止未获设备确认，请使用独立断电', 'en'))
        for original, english in neje_i18n.EN.items():
            fields = lambda text: {name for _, name, _, _ in Formatter().parse(text) if name}
            self.assertEqual(fields(original), fields(english), original)

    def test_language_preference_survives_device_and_window_updates(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'settings.json'
            desktop_settings.save_language('en', path)
            desktop_settings.save('NEJE 5 Plus', 'device-A', path)
            desktop_settings.save_window('720x300+10+20', path)
            self.assertEqual(desktop_settings.load_language(path), 'en')
            self.assertEqual(desktop_settings.load(path)['address'], 'device-A')
            desktop_settings.save('NEJE 5 Plus', 'device-B', path)
            self.assertEqual(desktop_settings.load_window(path), '720x300+10+20')
            with self.assertRaises(ValueError):
                desktop_settings.save_language('fr', path)

    def test_switch_relabels_widgets_without_starting_or_stopping_bridge(self):
        app = neje_app.App.__new__(neje_app.App)
        app.language = 'zh'
        app.language_choice = Mock()
        app.language_choice.get.return_value = 'English'
        for name in ('language_label', 'subtitle', 'safety_label', 'advanced_toggle',
                     'logs_toggle', 'scan_btn', 'test_btn', 'steps_btn', 'logs_btn',
                     'logbox', 'estop_btn', 'create_btn'):
            setattr(app, name, Mock())
        for name in ('setup', 'selected', 'verdict'):
            setattr(app, name, Mock())
        app.supervisor = Mock()
        app.supervisor.last_verdict = None
        app.device_display_name = 'NEJE 5 Plus'
        app._refresh_once = Mock()
        app._fit_language_width = Mock()
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(desktop_settings, 'PATH', Path(folder) / 'settings.json'), \
             patch.object(neje_app.device_setup, 'lightburn_tcp_device', return_value=[]):
            app.on_language_change()
            self.assertEqual(desktop_settings.load_language(), 'en')
        self.assertEqual(app.selected.set.call_args.args[0], 'Device: NEJE 5 Plus')
        self.assertEqual(app.estop_btn.configure.call_args.kwargs['text'], 'Stop machine (software)')
        app.supervisor.start.assert_not_called()
        app.supervisor.stop.assert_not_called()
        app._refresh_once.assert_called_once()

    def test_supervisor_messages_follow_current_app_language(self):
        app = neje_app.App.__new__(neje_app.App)
        app.language = 'en'
        supervisor = neje_app.Supervisor.__new__(neje_app.Supervisor)
        supervisor.translator = app._
        self.assertEqual(supervisor._t('已经在运行'), 'Already running')
        app.language = 'zh'
        self.assertEqual(supervisor._t('已经在运行'), '已经在运行')

    def test_first_launch_uses_os_language_and_english_setup_is_readable(self):
        with patch.object(neje_i18n.locale, 'getlocale', return_value=('zh_CN', 'UTF-8')):
            self.assertEqual(neje_i18n.preferred_language(), 'zh')
        with patch.object(neje_i18n.locale, 'getlocale', return_value=('en_US', 'UTF-8')):
            self.assertEqual(neje_i18n.preferred_language(), 'en')
        with patch.object(device_setup, '_devices', return_value=[]):
            changed, message = device_setup.add_tcp_device(apply=False, language='en')
        self.assertTrue(changed)
        self.assertIn('BLE Bridge', message)
        self.assertIn('TCP/Ethernet', device_setup.add_instructions(language='en'))


if __name__ == '__main__':
    unittest.main()
