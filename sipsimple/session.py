from thread import allocate_lock
from datetime import datetime
from collections import deque

from zope.interface import implements

from application.notification import IObserver, NotificationCenter, NotificationData
from application.python.util import Singleton
from application.system import default_host_ip

from sipsimple.engine import Engine
from sipsimple.core import Invitation, SDPSession, SDPMedia, SDPConnection, RTPTransport, AudioTransport, WaveFile

class TimestampedNotificationData(NotificationData):

    def __init__(self, **kwargs):
        self.timestamp = datetime.now()
        NotificationData.__init__(self, **kwargs)


class Session(object):
    """Represents a session.
       Attributes:
       state: The state of the object as a string
       remote_user_agent: The user agent of the remote party, once detected
       rtp_options: the RTPTransport options fetched from the SessionManager
           at object creation."""

    def __init__(self):
        """Instatiates a new Session object for an incoming or outgoing
           session. Initially the object is in the NULL state."""
        self.session_manager = SessionManager()
        self.notification_center = NotificationCenter()
        self.rtp_options = self.session_manager.rtp_config.__dict__.copy()
        self.state = "NULL"
        self.remote_user_agent = None
        self.on_hold_by_local = False
        self.on_hold_by_remote = False
        self._lock = allocate_lock()
        self._inv = None
        self._audio_sdp_index = -1
        self._audio_transport = None
        self._queue = deque()
        self._ringtone = None

    # user interface
    def new(self, callee_uri, credentials, route=None, use_audio=False):
        """Creates a new session to the callee with the requested stream(s).
           Moves the object from the NULL into the CALLING state."""
        self._lock.acquire()
        try:
            if self.state != "NULL":
                raise RuntimeError("This method can only be called while in the NULL state")
            if not any([use_audio]):
                raise RuntimeError("No media stream requested")
            sdp_index = 0
            local_address = self.rtp_options["local_rtp_address"]
            local_sdp = SDPSession(local_address, connection=SDPConnection(local_address))
            if use_audio:
                self._audio_sdp_index = sdp_index
                sdp_index += 1
                local_sdp.media.append(self._init_audio())
            self._inv = Invitation(credentials, callee_uri, route=route)
            self._inv.set_offered_local_sdp(local_sdp)
            self.session_manager.inv_mapping[self._inv] = self
            self._inv.send_invite()
            self._change_state("CALLING")
            self.notification_center.post_notification("SCSessionNewOutgoing", self, TimestampedNotificationData(audio_proposed=use_audio))
        except:
            self._stop_media()
            self._audio_sdp_index = -1
            raise
        finally:
            self._lock.release()

    def accept(self, use_audio=False):
        """Accept an incoming session, using the requested stream(s).
           Moves the object from the INCOMING to the ACCEPTING state."""
        self._lock.acquire()
        try:
            if self.state != "INCOMING":
                raise RuntimeError("This method can only be called while in the INCOMING state")
            remote_sdp = self._inv.get_offered_remote_sdp()
            local_address = self.rtp_options["local_rtp_address"]
            local_sdp = SDPSession(local_address, connection=SDPConnection(local_address), media=len(remote_sdp.media)*[None], start_time=remote_sdp.start_time, stop_time=remote_sdp.stop_time)
            sdp_media_todo = range(len(remote_sdp.media))
            if use_audio:
                for audio_sdp_index, sdp_media in enumerate(remote_sdp.media):
                    if sdp_media.media == "audio":
                        sdp_media_todo.remove(audio_sdp_index)
                        self._audio_sdp_index = audio_sdp_index
                        local_sdp.media[audio_sdp_index] = (self._init_audio(remote_sdp))
                        break
                if self._audio_sdp_index == -1:
                    raise RuntimeError("Use of audio requested, but audio was not proposed by remote party")
            if len(sdp_media_todo) == len(remote_sdp.media):
                raise RuntimeError("None of the streams proposed by the remote party is enabled")
            for reject_media_index in sdp_media_todo:
                remote_media = remote_sdp.media[reject_media_index]
                local_sdp.media[reject_media_index] = SDPMedia(remote_media.media, 0, remote_media.transport, formats=remote_media.formats, attributes=remote_media.attributes)
            self._inv.set_offered_local_sdp(local_sdp)
            self._inv.accept_invite()
            self._change_state("ACCEPTING")
        except:
            self._stop_media()
            self._audio_sdp_index = -1
            raise
        finally:
            self._lock.release()

    def reject(self):
        """Rejects an incoming session. Moves the object from the INCOMING to
           the TERMINATING state."""
        if self.state != "INCOMING":
            raise RuntimeError("This method can only be called while in the INCOMING state")
        self.terminate()

    def add_audio(self):
        """Add an audio stream to an already established session."""
        self._lock.acquire()
        try:
            if self.state != "ESTABLISHED":
                raise RuntimeError("This method can only be called while in the ESTABLISHED state")
            if self._audio_transport is not None:
                raise RuntimeError("An audio stream is already active whithin this session")
            # TODO: implement and emit SCSessionGotStreamProposal
        finally:
            self._lock.release()

    def accept_proposal(self):
        """Accept a proposal of stream(s) being added. Moves the object from
           the PROPOSED state to the ESTABLISHED state."""
        self._lock.acquire()
        try:
            if self.state != "PROPOSED":
                raise RuntimeError("This method can only be called while in the PROPOSED state")
            # TODO: implement and emit SCSessionAcceptedStreamProposal
        finally:
            self._lock.release()

    def reject_proposal(self):
        """Reject a proposal of stream(s) being added. Moves the object from
           the PROPOSED state to the ESTABLISHED state."""
        self._lock.acquire()
        try:
            if self.state != "PROPOSED":
                raise RuntimeError("This method can only be called while in the PROPOSED state")
            self._inv.respond_to_reinvite(488)
            self._change_state("ESTABLISHED")
            self.notification_center.post_notification("SCSessionRejectedStreamProposal", self, TimestampedNotificationData(originator="local"))
        finally:
            self._lock.release()

    def hold(self):
        """Put an established session on hold. This moves the object from the
           ESTABLISHED state to the ONHOLD state."""
        self._lock.acquire()
        try:
            if self.state != "ESTABLISHED":
                raise RuntimeError("Session is not active")
            self._queue.append("hold")
            if len(self._queue) == 1:
                self._process_queue()
        finally:
            self._lock.release()

    def unhold(self):
        """Takes a session that was previous put on hold out of hold. This
           moves the object from the ONHOLD state to the ESTABLISHED state."""
        self._lock.acquire()
        try:
            if self.state != "ESTABLISHED":
                raise RuntimeError("Session is not active")
            self._queue.append("unhold")
            if len(self._queue) == 1:
                self._process_queue()
        finally:
            self._lock.release()

    def terminate(self):
        """Terminates the session from whatever state it is in.
           Moves the object to the TERMINATING state."""
        self._lock.acquire()
        try:
            if self.state in ["NULL", "TERMINATING", "TERMINATED"]:
                return
            if self._inv.state != "DISCONNECTING":
                self._inv.disconnect()
            self._change_state("TERMINATING")
            self.notification_center.post_notification("SCSessionWillEnd", self, TimestampedNotificationData())
        finally:
            self._lock.release()

    def _change_state(self, new_state):
        prev_state = self.state
        self.state = new_state
        if prev_state != new_state:
            if new_state == "INCOMING":
                if self._ringtone is not None:
                    try:
                        self._ringtone.start(loop_count=0, pause_time=0.5)
                    except:
                        pass
            if prev_state == "INCOMING":
                if self._ringtone is not None:
                    self._ringtone.stop()
                    self._ringtone = None
            self.notification_center.post_notification("SCSessionChangedState", self, TimestampedNotificationData(prev_state=prev_state, state=new_state))

    def _process_queue(self):
        was_on_hold = self.on_hold_by_local
        while self._queue:
            command = self._queue.popleft()
            if command == "hold":
                if self.on_hold_by_local:
                    continue
                if self._audio_transport is not None and self._audio_transport.is_active:
                    Engine().disconnect_audio_transport(self._audio_transport)
                local_sdp = self._make_next_sdp(True, True)
                self.on_hold_by_local = True
                break
            elif command == "unhold":
                if not self.on_hold_by_local:
                    continue
                if self._audio_transport is not None and self._audio_transport.is_active:
                    Engine().connect_audio_transport(self._audio_transport)
                local_sdp = self._make_next_sdp(True, False)
                self.on_hold_by_local = False
                break
        self._inv.set_offered_local_sdp(local_sdp)
        self._inv.send_reinvite()
        if not was_on_hold and self.on_hold_by_local:
            self.notification_center.post_notification("SCSessionGotHoldRequest", self, TimestampedNotificationData(originator="local"))
        elif was_on_hold and not self.on_hold_by_local:
            self.notification_center.post_notification("SCSessionGotUnholdRequest", self, TimestampedNotificationData(originator="local"))

    def _init_audio(self, remote_sdp=None):
        """Initialize everything needed for an audio stream and return a
           SDPMedia object describing it. Called internally."""
        rtp_transport = RTPTransport(**self.rtp_options)
        if remote_sdp is None:
            self._audio_transport = AudioTransport(rtp_transport)
        else:
            self._audio_transport = AudioTransport(rtp_transport, remote_sdp, self._audio_sdp_index)
        self.session_manager.audio_transport_mapping[self._audio_transport] = self
        return self._audio_transport.get_local_media(remote_sdp is None)

    def _update_media(self, local_sdp, remote_sdp):
        """Update the media stream(s) according to the newly negotiated SDP.
           This will start, stop or change the stream(s). Called by
           SessionManager."""
        if self._audio_transport:
            if local_sdp.media[self._audio_sdp_index].port and remote_sdp.media[self._audio_sdp_index].port:
                self._update_audio(local_sdp, remote_sdp)
            else:
                self._stop_audio()

    def _update_audio(self, local_sdp, remote_sdp):
        """Update the audio stream. Will be called locally from
           _update_media()."""
        if self._audio_transport.is_active:
            # TODO: check for ip/port/codec changes and restart AudioTransport if needed
            was_on_hold = self.on_hold_by_remote
            new_direction = local_sdp.media[self._audio_sdp_index].get_direction()
            self.on_hold_by_remote = "send" not in new_direction
            self._audio_transport.update_direction(new_direction)
            if not was_on_hold and self.on_hold_by_remote:
                self.notification_center.post_notification("SCSessionGotHoldRequest", self, TimestampedNotificationData(originator="remote"))
            elif was_on_hold and not self.on_hold_by_remote:
                self.notification_center.post_notification("SCSessionGotUnholdRequest", self, TimestampedNotificationData(originator="remote"))
        else:
            self._audio_transport.start(local_sdp, remote_sdp, self._audio_sdp_index)
            Engine().connect_audio_transport(self._audio_transport)

    def _stop_media(self):
        """Stop all media streams. This will be called by SessionManager when
           the session ends."""
        if self._audio_transport:
            self._stop_audio()

    def _stop_audio(self):
        """Stop the audio stream. This will be called locally, either from
        _update_media() or _stop_media()."""
        if self._audio_transport.is_active:
            Engine().disconnect_audio_transport(self._audio_transport)
            self._audio_transport.stop()
        del self.session_manager.audio_transport_mapping[self._audio_transport]
        self._audio_transport = None

    def _cancel_media(self):
        if self._audio_transport is not None and not self._audio_transport.is_active:
            self._stop_audio()

    def send_dtmf(self, digit):
        if self._audio_transport is None or not self._audio_transport.is_active:
            raise RuntimeError("This session does not have an audio stream to transmit DMTF over")
        self._audio_transport.send_dtmf(digit)

    def _make_next_sdp(self, is_offer, on_hold=False):
        local_sdp = self._inv.get_active_local_sdp()
        local_sdp.version += 1
        if self._audio_transport is not None:
            if is_offer:
                if "send" in self._audio_transport.direction:
                    direction = ("sendonly" if on_hold else "sendrecv")
                else:
                    direction = ("inactive" if on_hold else "recvonly")
            else:
                direction = None
            local_sdp.media[self._audio_sdp_index] = self._audio_transport.get_local_media(is_offer, direction)
        return local_sdp


class RTPConfiguration(object):

    def __init__(self, local_rtp_address=default_host_ip, use_srtp=False, srtp_forced=False, use_ice=False, ice_stun_address=None, ice_stun_port=3478, *args, **kwargs):
        self.local_rtp_address = local_rtp_address
        self.use_srtp = use_srtp
        self.srtp_forced = srtp_forced
        self.use_ice = use_ice
        self.ice_stun_address = ice_stun_address
        self.ice_stun_port = ice_stun_port


class RingtoneConfiguration(object):

    def __init__(self):
        self.default_ringtone = None
        self._user_host_mapping = {}

    def add_ringtone_for_sipuri(self, sipuri, ringtone):
        self._user_host_mapping[(sipuri.user, sipuri.host)] = ringtone

    def remove_sipuri(self, sipuri):
        del self._user_host_mapping[(sipuri.user, sipuri.host)]

    def get_ringtone_for_sipuri(self, sipuri):
        return self._user_host_mapping.get((sipuri.user, sipuri.host), self.default_ringtone)


class SessionManager(object):
    """The one and only SessionManager, a singleton.
       The application needs to create this and then pass its handle_event
       method to the Engine as event_handler.
       Attributes:
       rtp_config: RTPConfiguration object
       inv_mapping: A dictionary mapping Invitation objects to Session
           objects."""
    __metaclass__ = Singleton
    implements(IObserver)

    def __init__(self):
        """Creates a new SessionManager object."""
        self.rtp_config = RTPConfiguration()
        self.inv_mapping = {}
        self.audio_transport_mapping = {}
        self.notification_center = NotificationCenter()
        self.notification_center.add_observer(self, "SCInvitationChangedState")
        self.notification_center.add_observer(self, "SCInvitationGotSDPUpdate")
        self.notification_center.add_observer(self, "SCAudioTransportGotDTMF")
        self.ringtone_config = RingtoneConfiguration()

    def handle_notification(self, notification):
        """Catches the SCInvitationChangedState and SCInvitationGotSDPUpdate
           notifications and takes the appropriate action on the associated
           Session object. If needed, it will also post a notification related
           to the Session for consumption by the application."""
        handler = getattr(self, '_handle_%s' % notification.name, None)
        if handler is not None:
            handler(notification.sender, notification.data)

    def _handle_SCInvitationChangedState(self, inv, data):
        if data.state == "INCOMING":
            remote_media = [media.media for media in inv.get_offered_remote_sdp().media if media.port != 0]
            # TODO: check if the To header/request URI is one of ours
            if not any(supported_media in remote_media for supported_media in ["audio"]):
                inv.disconnect(415)
            else:
                inv.respond_to_invite_provisionally(180)
                session = Session()
                session._inv = inv
                session.remote_user_agent = data.headers.get("User-Agent", None)
                self.inv_mapping[inv] = session
                caller_uri = inv.caller_uri
                ringtone = self.ringtone_config.get_ringtone_for_sipuri(inv.caller_uri)
                if ringtone is not None:
                    session._ringtone = WaveFile(ringtone)
                session._change_state("INCOMING")
                self.notification_center.post_notification("SCSessionNewIncoming", session, TimestampedNotificationData(has_audio="audio" in remote_media))
        else:
            session = self.inv_mapping.get(inv, None)
            if session is None:
                return
            session._lock.acquire()
            try:
                prev_session_state = session.state
                if data.state == "EARLY" and inv.is_outgoing and hasattr(data, "code") and data.code == 180:
                    self.notification_center.post_notification("SCSessionGotRingIndication", session, TimestampedNotificationData())
                elif data.state == "CONNECTING":
                    self.notification_center.post_notification("SCSessionWillStart", session, TimestampedNotificationData())
                    if inv.is_outgoing:
                        session.remote_user_agent = data.headers.get("Server", None)
                        if session.remote_user_agent is None:
                            session.remote_user_agent = data.headers.get("User-Agent", None)
                elif data.state == "CONFIRMED":
                    session._change_state("ESTABLISHED")
                    if data.prev_state == "CONNECTING":
                        self.notification_center.post_notification("SCSessionDidStart", session, TimestampedNotificationData())
                    # TODO: if data.prev_state == "REINVITING" and a stream is being added,
                    #       evaluate there sult and emit either SCSessionAcceptedStreamProposal
                    #       or SCSessionRejectedStreamProposal
                    if session._queue:
                        session._process_queue()
                elif data.state == "REINVITED":
                    current_remote_sdp = inv.get_active_remote_sdp()
                    proposed_remote_sdp = inv.get_offered_remote_sdp()
                    if proposed_remote_sdp.version == current_remote_sdp.version:
                        if current_remote_sdp != proposed_remote_sdp:
                            # same version, but not identical SDP
                            inv.respond_to_reinvite(488)
                        else:
                            # same version, same SDP, respond with the already present local SDP
                            inv.set_offered_local_sdp(inv.get_active_local_sdp())
                            inv.respond_to_reinvite(200)
                    elif proposed_remote_sdp.version == current_remote_sdp.version + 1:
                        for attr in ["user", "id", "net_type", "address_type", "address"]:
                            if getattr(proposed_remote_sdp, attr) != getattr(current_remote_sdp, attr):
                                # difference in contents of o= line
                                inv.respond_to_reinvite(488)
                                return
                        current_remote_media = [media.media for media in current_remote_sdp.media if media.port != 0]
                        proposed_remote_media = [media.media for media in proposed_remote_sdp.media if media.port != 0]
                        notification_dict = {}
                        notification_dict["has_audio"] = "audio" not in current_remote_media and "audio" in proposed_remote_media
                        if True in notification_dict.values():
                            inv.respond_to_reinvite(180)
                            session._change_state("PROPOSED")
                            self.notification_center.post_notification("SCSessionGotStreamProposal", session, TimestampedNotificationData(**notification_dict))
                        else:
                            inv.set_offered_local_sdp(session._make_next_sdp(False))
                            inv.respond_to_reinvite(200)
                    else:
                        # version increase is not exactly one more
                        inv.respond_to_reinvite(488)
                elif data.state == "DISCONNECTED":
                    del self.inv_mapping[inv]
                    if hasattr(data, "headers"):
                        if session.remote_user_agent is None:
                            session.remote_user_agent = data.headers.get("Server", None)
                        if session.remote_user_agent is None:
                            session.remote_user_agent = data.headers.get("User-Agent", None)
                    session._stop_media()
                    session._inv = None
                    session._change_state("TERMINATED")
                    if prev_session_state != "TERMINATING" and data.prev_state != "CONFIRMED":
                        self.notification_center.post_notification("SCSessionDidFail", session, TimestampedNotificationData())
                    self.notification_center.post_notification("SCSessionDidEnd", session, TimestampedNotificationData())
            finally:
                session._lock.release()

    def _handle_SCInvitationGotSDPUpdate(self, inv, data):
        session = self.inv_mapping.get(inv, None)
        if session is None:
            return
        session._lock.acquire()
        try:
            if data.succeeded:
                session._update_media(data.local_sdp, data.remote_sdp)
            else:
                session._cancel_media()
        finally:
            session._lock.release()

    def _handle_SCAudioTransportGotDTMF(self, audio_transport, data):
        session = self.audio_transport_mapping.get(audio_transport, None)
        if session is not None:
            self.notification_center.post_notification("SCSessionGotDTMF", session, data)


__all__ = ["SessionManager", "Session"]