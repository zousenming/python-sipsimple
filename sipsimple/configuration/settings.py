import os

from sipsimple.configuration import Setting, SettingsGroup, SettingsObject
from sipsimple.configuration.datatypes import AbsolutePath, DataPath, ImageDepth, LocalIPAddress, NonNegativeInteger, Port, PortRange, Resolution, SampleRate, TLSProtocol, Transports


__all__ = ['SIPSimpleSettings']


class AudioSettings(SettingsGroup):
    enabled = Setting(type=bool, default=True)
    input_device = Setting(type=str, default=None, nillable=True)
    output_device = Setting(type=str, default=None, nillable=True)
    echo_delay = Setting(type=NonNegativeInteger, default=200)
    recordings_directory = Setting(type=DataPath, default=DataPath('history'))
    sample_rate = Setting(type=SampleRate, default=32)
    playback_dtmf = Setting(type=bool, default=True)


class ChatSettings(SettingsGroup):
    enabled = Setting(type=bool, default=True)
    auto_accept = Setting(type=bool, default=False)
    message_received_sound = Setting(type=AbsolutePath, default=AbsolutePath.get_application_path('message_received.wav'), nillable=True)
    message_sent_sound = Setting(type=AbsolutePath, default=AbsolutePath.get_application_path('message_sent.wav'), nillable=True)


class DesktopSharingSettings(SettingsGroup):
    enabled = Setting(type=bool, default=True)
    auto_accept = Setting(type=bool, default=False)
    depth = Setting(type=ImageDepth, default=8)
    resolution = Setting(type=Resolution, default=Resolution(width=1024, height=768))
    client_command = Setting(type=AbsolutePath, default=None, nillable=True)
    server_command = Setting(type=AbsolutePath, default=None, nillable=True)


class FileTransferSettings(SettingsGroup):
    enabled = Setting(type=bool, default=True)
    directory = Setting(type=DataPath, default=DataPath('file_transfers'))
    auto_accept = Setting(type=bool, default=False)
    file_received_sound = Setting(type=AbsolutePath, default=AbsolutePath.get_application_path('file_received.wav'), nillable=True)
    file_sent_sound = Setting(type=AbsolutePath, default=AbsolutePath.get_application_path('file_sent.wav'), nillable=True)


class LoggingSettings(SettingsGroup):
    directory = Setting(type=DataPath, default=DataPath('logs'))
    trace_sip = Setting(type=bool, default=False)
    trace_pjsip = Setting(type=bool, default=False)
    trace_msrp = Setting(type=bool, default=False)
    trace_xcap = Setting(type=bool, default=False)
    pjsip_level = Setting(type=NonNegativeInteger, default=5)


class RingtoneSettings(SettingsGroup):
    audio_inbound = Setting(type=AbsolutePath, default=AbsolutePath.get_application_path('ring_inbound.wav'), nillable=True)
    audio_outbound = Setting(type=AbsolutePath, default=AbsolutePath.get_application_path('ring_outbound.wav'), nillable=True)
    chat_inbound = Setting(type=AbsolutePath, default=AbsolutePath.get_application_path('ring_inbound.wav'), nillable=True)
    chat_outbound = Setting(type=AbsolutePath, default=AbsolutePath.get_application_path('ring_outbound.wav'), nillable=True)
    file_transfer_inbound = Setting(type=AbsolutePath, default=AbsolutePath.get_application_path('ring_inbound.wav'), nillable=True)
    file_transfer_outbound = Setting(type=AbsolutePath, default=AbsolutePath.get_application_path('ring_outbound.wav'), nillable=True)
    desktop_sharing_inbound = Setting(type=AbsolutePath, default=AbsolutePath.get_application_path('ring_inbound.wav'), nillable=True)
    desktop_sharing_outbound = Setting(type=AbsolutePath, default=AbsolutePath.get_application_path('ring_outbound.wav'), nillable=True)


class RTPSettings(SettingsGroup):
    port_range = Setting(type=PortRange, default=PortRange(50000, 50400))


class SIPSettings(SettingsGroup):
    local_udp_port = Setting(type=Port, default=0)
    local_tcp_port = Setting(type=Port, default=0)
    local_tls_port = Setting(type=Port, default=0)
    transports = Setting(type=Transports, default=('tls', 'tcp', 'udp'))


class TLSSettings(SettingsGroup):
    ca_list_file = Setting(type=DataPath, default=DataPath('tls/ca.crt'))
    certificate_file = Setting(type=DataPath, default=None, nillable=True)
    private_key_file = Setting(type=DataPath, default=None, nillable=True)
    protocol = Setting(type=TLSProtocol, default='TLSv1')
    verify_server = Setting(type=bool, default=True)
    timeout = Setting(type=NonNegativeInteger, default=1000)


class SIPSimpleSettings(SettingsObject):
    __section__ = 'Global'
    __id__ = 'SIPSimple'
    
    data_directory = Setting(type=AbsolutePath, default=os.path.expanduser('~/.sipclient'))
    default_account = Setting(type=str, default='bonjour', nillable=True)
    local_ip = Setting(type=LocalIPAddress, default=LocalIPAddress())
    user_agent = Setting(type=str, default='sip2sip')

    audio = AudioSettings
    desktop_sharing = DesktopSharingSettings
    file_transfer = FileTransferSettings
    logging = LoggingSettings
    ringtone = RingtoneSettings
    rtp = RTPSettings
    sip = SIPSettings
    tls = TLSSettings

