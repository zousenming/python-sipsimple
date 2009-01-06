import sys
import os
import glob
from optparse import OptionValueError, OptionParser
from ConfigParser import NoSectionError

from application.configuration import ConfigSection, ConfigFile, datatypes
from application.process import process

from msrplib.connect import MSRPRelaySettings

from pypjua import SIPURI, Route
from pypjua.clients.lookup import lookup_srv
from pypjua.clients.lookup import IPAddressOrHostname
from pypjua.clients.cpim import SIPAddress

process._system_config_directory = os.path.expanduser("~/.sipclient")
config_ini = os.path.join(process._system_config_directory, 'config.ini')

# disable twisted debug messages (enabled by python-application)
from twisted.python import log
if log.defaultObserver is not None:
    log.defaultObserver.stop()
    log.defaultObserver = log.DefaultObserver()
    log.defaultObserver.start()

class GeneralConfig(ConfigSection):
    _datatypes = {"listen_udp": datatypes.NetworkAddress,
                  "trace_pjsip": datatypes.Boolean,
                  "trace_sip": datatypes.Boolean,
                  "auto_accept_file_transfers": datatypes.Boolean}
    listen_udp = datatypes.NetworkAddress("any")
    trace_pjsip = False
    trace_sip = False
    file_transfers_directory = os.path.join(process._system_config_directory, 'file_transfers')
    auto_accept_file_transfers = False

class AccountConfig(ConfigSection):
    _datatypes = {"sip_address": str,
                  "password": str,
                  "display_name": str,
                  "outbound_proxy": IPAddressOrHostname,
                  "msrp_relay": str}
    sip_address = None
    password = None
    display_name = None
    outbound_proxy = None
    msrp_relay = "auto"

class AudioConfig(ConfigSection):
    _datatypes = {"disable_sound": datatypes.Boolean}
    disable_sound = False

def get_download_path(name):
    path = os.path.join(GeneralConfig.file_transfers_directory, name)
    if os.path.exists(path):
        all = [int(x[len(path)+1:]) for x in glob.glob(path + '.*')]
        if not all:
            return path + '.1'
        else:
            return path + '.' + str(max(all)+1)
    return path

def parse_outbound_proxy(option, opt_str, value, parser):
    try:
        parser.values.outbound_proxy = IPAddressOrHostname(value)
    except ValueError, e:
        raise OptionValueError(e.message)

def _parse_msrp_relay(value):
    if value in ['auto', 'srv', 'none']:
        return value
    try:
        return IPAddressOrHostname(value)
    except ValueError, e:
        raise OptionValueError(e.message)

def parse_msrp_relay(option, opt_str, value, parser):
    parser.values.msrp_relay = _parse_msrp_relay(value)

def parse_options(usage, description):
    configuration = ConfigFile(config_ini)
    configuration.read_settings("Audio", AudioConfig)
    configuration.read_settings("General", GeneralConfig)

    parser = OptionParser(usage=usage, description=description)
    parser.print_usage = parser.print_help
    parser.add_option("-a", "--account-name", type="string",
                      help=('The account name from which to read account settings. '
                            'Corresponds to section Account_NAME in the configuration file.'))
    parser.add_option('--show-config', action='store_true',
                      help = ('Show settings from the configuration and exit; '
                              'use together with --account-name option'))
    parser.add_option("--sip-address", type="string", help="SIP login account")
    parser.add_option("-p", "--password", type="string",
                      help="Password to use to authenticate the local account.")
    parser.add_option("-n", "--display-name", type="string",
                      help="Display name to use for the local account.")

    help = ('Use the outbound SIP proxy; '
            'if "auto", discover the SIP proxy through SRV and A '
            'records lookup on the domain part of user SIP URI.')
    parser.add_option("-o", "--outbound-proxy", type="string",
                      action="callback", callback=parse_outbound_proxy, help=help, metavar="IP[:PORT]")

    parser.add_option("-m", "--trace-msrp", action="store_true", default=False,
                      help="Dump the raw contents of incoming and outgoing MSRP messages.")
    parser.add_option("-s", "--trace-sip", action="store_true", default=GeneralConfig.trace_sip,
                      help="Dump the raw contents of incoming and outgoing SIP messages.")
    parser.add_option("-j", "--trace-pjsip", action="store_true", default=GeneralConfig.trace_pjsip,
                      help="Print PJSIP logging output.")

    help=('Use the MSRP relay; '
          'if "srv", do SRV lookup on domain part of the target SIP URI, '
          'use user\'s domain if SRV lookup was not successful; '
          'if "none", the direct connection is performed; '
          'if "auto", use "srv" for incoming connections and "none" for outgoing; '
          'default is "auto".')
    parser.add_option("-r", "--msrp-relay", type='string',
                      action="callback", callback=parse_msrp_relay, help=help, metavar='IP[:PORT]')
    parser.add_option("-S", "--disable-sound", action="store_true", default=AudioConfig.disable_sound,
                      help="Do not initialize the soundcard (by default the soundcard is enabled).")
    #parser.add_option("-y", '--auto-accept-all', action='store_true', default=False, help=SUPPRESS_HELP)
    parser.add_option('--auto-accept-files', action='store_true',
                      help='Accept all incoming file transfers without bothering user.')

    options, args = parser.parse_args()

    if options.account_name is None:
        account_section = 'Account'
    else:
        account_section = 'Account_%s' % options.account_name

    options.use_bonjour = options.account_name == 'bonjour'

    if options.account_name not in [None, 'bonjour'] and account_section not in configuration.parser.sections():
        msg = "Section [%s] was not found in the configuration file %s" % (account_section, config_ini)
        raise RuntimeError(msg)
    configuration.read_settings(account_section, AccountConfig)
    default_options = dict(outbound_proxy=AccountConfig.outbound_proxy,
                           msrp_relay=_parse_msrp_relay(AccountConfig.msrp_relay),
                           sip_address=AccountConfig.sip_address,
                           password=AccountConfig.password,
                           display_name=AccountConfig.display_name,
                           local_ip=GeneralConfig.listen_udp[0],
                           local_port=GeneralConfig.listen_udp[1],
                           auto_accept_files=GeneralConfig.auto_accept_file_transfers)
    if options.show_config:
        print 'Configuration file: %s' % config_ini
        for config_section in [account_section, 'General', 'Audio']:
            try:
                options = configuration.parser.options(config_section)
            except NoSectionError:
                pass
            else:
                print '[%s]' % config_section
                for option in options:
                    if option in default_options.keys():
                        print '%s=%s' % (option, default_options[option])
        accounts = []
        for section in configuration.parser.sections():
            if section.startswith('Account') and account_section != section:
                if section == 'Account':
                    accounts.append('(default)')
                else:
                    accounts.append(section[8:])
        accounts.sort()
        print "Other accounts: %s" % ', '.join(accounts)
        sys.exit()

    options._update_loose(dict((name, value) for name, value in default_options.items() if getattr(options, name, None) is None))

    accounts = [(acc == 'Account') and 'default' or "'%s'" % acc[8:] for acc in configuration.parser.sections() if acc.startswith('Account')]
    accounts.sort()
    print "Accounts available: %s" % ', '.join(accounts)
    if options.account_name is None:
        print "Using default account: %s" % options.sip_address
    else:
        print "Using account '%s': %s" % (options.account_name, options.sip_address)

    if not options.use_bonjour:
        if not all([options.sip_address, options.password]):
            raise RuntimeError("No complete set of SIP credentials specified in config file and on commandline.")
    options.sip_address = SIPAddress.parse(options.sip_address)
    options.uri = SIPURI(user=options.sip_address.username, host=options.sip_address.domain, display=options.display_name)
    if args:
        options.target_address = SIPAddress.parse(args[0], default_domain = options.sip_address.domain)
        options.target_uri = SIPURI(user=options.target_address.username, host=options.target_address.domain)
        del args[0]
    else:
        options.target_address = None
        options.target_uri = None
    options.args = args

    if options.msrp_relay == 'auto':
        if options.target_uri is None:
            options.msrp_relay = 'srv'
        else:
            options.msrp_relay = 'none'
    if options.msrp_relay == 'srv':
        options.relay = MSRPRelaySettings(domain=options.sip_address.domain,
                                          username=options.sip_address.username, password=options.password)
    elif options.msrp_relay == 'none':
        options.relay = None
    else:
        host, port, is_ip = options.msrp_relay
        if is_ip or port is not None:
            options.relay = MSRPRelaySettings(domain=options.sip_address.domain, host=host, port=port,
                                              username=options.sip_address.username, password=options.password)
        else:
            options.relay = MSRPRelaySettings(domain=options.sip_address.domain,
                                              username=options.sip_address.username, password=options.password)
    if options.use_bonjour:
        options.route = None
    else:
        if options.outbound_proxy is None:
            proxy_host, proxy_port, proxy_is_ip = options.sip_address.domain, None, False
        else:
            proxy_host, proxy_port, proxy_is_ip = options.outbound_proxy
        options.route = Route(*lookup_srv(proxy_host, proxy_port, proxy_is_ip, 5060))
    return options
