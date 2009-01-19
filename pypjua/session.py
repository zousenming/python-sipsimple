import thread
from pypjua import *

# TODO: relocate this to somewhere else
import socket
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('1.2.3.4', 56))
        default_host_ip = s.getsockname()[0]
    finally:
        s.close()
        del s
except socket.error:
    default_host_ip = None
del socket

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
        self.session_manager = SessionManager._instance
        self.rtp_options = self.session_manager.rtp_config.__dict__.copy()
        self.state = "NULL"
        self.remote_user_agent = None
        self._lock = thread.allocate_lock()
        self._inv = None
        self._audio_sdp_index = -1
        self._audio_transport = None

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
            self.session_manager.session_mapping[self._inv] = self
            self.state = "CALLING"
            self._inv.set_state_CALLING()
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
            local_sdp = SDPSession(local_address, connection=SDPConnection(local_address), media=len(remote_sdp.media)*[None])
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
            for reject_media_index in sdp_media_todo:
                remote_media = remote_sdp.media[reject_media_index]
                local_sdp.media[reject_media_index] = SDPMedia(remote_media.media, 0, remote_media.transport, formats=remote_media.formats[:])
            self._inv.set_offered_local_sdp(local_sdp)
            self._inv.set_state_CONNECTING()
            self.state = "ACCEPTING"
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
        finally:
            self._lock.release()

    def accept_proposal(self):
        """Accept a proposal of stream(s) being added. Moves the object from
           the PROPOSED state to the ESTABLISHED state."""
        self._lock.acquire()
        try:
            if self.state != "PROPOSED":
                raise RuntimeError("This method can only be called while in the PROPOSED state")
        finally:
            self._lock.release()

    def reject_proposal(self):
        """Reject a proposal of stream(s) being added. Moves the object from
           the PROPOSED state to the ESTABLISHED state."""
        self._lock.acquire()
        try:
            if self.state != "PROPOSED":
                raise RuntimeError("This method can only be called while in the PROPOSED state")
        finally:
            self._lock.release()

    def place_on_hold(self):
        """Put an established session on hold. This moves the object from the
           ESTABLISHED state to the ONHOLD state."""
        self._lock.acquire()
        try:
            if self.state != "ESTABLISHED":
                raise RuntimeError("This method can only be called while in the ESTABLISHED state")
        finally:
            self._lock.release()

    def take_out_of_hold(self):
        """Takes a session that was previous put on hold out of hold. This
           moves the object from the ONHOLD state to the ESTABLISHED state."""
        self._lock.acquire()
        try:
            if self.state != "ONHOLD":
                raise RuntimeError("This method can only be called while in the ONHOLD state")
        finally:
            self._lock.release()

    def terminate(self):
        """Terminates the session from whatever state it is in.
           Moves the object to the TERMINATING state."""
        self._lock.acquire()
        try:
            if self.state in ["NULL", "TERMINATING", "TERMINATED"]:
                raise RuntimeError("This method cannot be called while in the NULL or TERMINATED states")
            self.state = "TERMINATING"
            self._inv.set_state_DISCONNECTED()
        finally:
            self._lock.release()

    def _init_audio(self, remote_sdp=None):
        """Initialize everything needed for an audio stream and return a
           SDPMedia object describing it. Called internally."""
        rtp_transport = RTPTransport(**self.rtp_options)
        if remote_sdp is None:
            self._audio_transport = AudioTransport(rtp_transport)
        else:
            self._audio_transport = AudioTransport(rtp_transport, remote_sdp, self._audio_sdp_index)
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
            pass
        else:
            self._audio_transport.start(local_sdp, remote_sdp, self._audio_sdp_index)
            Engine._instance.connect_audio_transport(self._audio_transport)

    def _stop_media(self):
        """Stop all media streams. This will be called by SessionManager when
           the session ends."""
        if self._audio_transport:
            self._stop_audio()

    def _stop_audio(self):
        """Stop the audio stream. This will be called locally, either from
        _update_media() or _stop_media()."""
        Engine._instance.disconnect_audio_transport(self._audio_transport)
        self._audio_transport.stop()
        self._audio_transport = None


class RTPConfiguration(object):

    def __init__(self, local_rtp_address=default_host_ip, use_srtp=False, srtp_forced=False, use_ice=False, ice_stun_address=None, ice_stun_port=3478, *args, **kwargs):
        self.local_rtp_address = local_rtp_address
        self.use_srtp = use_srtp
        self.srtp_forced = srtp_forced
        self.use_ice = use_ice
        self.ice_stun_address = ice_stun_address
        self.ice_stun_port = ice_stun_port


class SessionManager(object):
    """The one and only SessionManager, a singleton.
       The application needs to create this and then pass its handle_event
       method to the Engine as event_handler.
       Attributes:
       session_mapping: A dictionary mapping Invitation objects to Session
           objects."""
    _instance = None

    def __new__(cls, *args, **kwargs):
        """Needed singleton pattern."""
        if SessionManager._instance is None:
            SessionManager._instance = object.__new__(cls, *args, **kwargs)
        return SessionManager._instance

    def __init__(self, event_handler, *args, **kwargs):
        """Creates a new SessionManager object or returns the already created
           This needs to know the application event_handler so it can insert
           itself between pypjua and the application. The other arguments are
           needed when creating a RTPTransport object."""
        self.event_handler = event_handler
        self.rtp_config = RTPConfiguration(*args, **kwargs)
        self.session_mapping = {}

    def handle_event(self, event_name, **kwargs):
        """Catches the Invitation_state event and takes the appropriate action
           on the associated Session object. If needed, it will also emit an
           event related to the Session for consumption by the application."""
        if event_name == "Invitation_state":
            inv = kwargs.pop("obj")
            prev_state = kwargs.pop("prev_state")
            state = kwargs.pop("state")
            prev_sdp_state = kwargs.pop("prev_sdp_state")
            sdp_state = kwargs.pop("sdp_state")
            sdp_negotiated = kwargs.pop("sdp_negotiated", None)
            if state == "INCOMING":
                remote_media = [media.media for media in inv.get_offered_remote_sdp().media]
                if not any(supported_media in remote_media for supported_media in ["audio"]):
                    inv.set_state_DISCONNECTED(415)
                else:
                    inv.set_state_EARLY(180)
                    session = Session()
                    session.state = "INCOMING"
                    session._inv = inv
                    session.remote_user_agent = kwargs["headers"].get("User-Agent", None)
                    self.session_mapping[inv] = session
                    kwargs["obj"] = session
                    kwargs["prev_state"] = "NULL"
                    kwargs["state"] = session.state
                    kwargs["audio_proposed"] = "audio" in remote_media
                    self.event_handler("Session_state", **kwargs)
            else:
                session = self.session_mapping.get(inv, None)
                if session is None:
                    return
                session._lock.acquire()
                prev_session_state = session.state
                if sdp_state == "DONE" and sdp_state != prev_sdp_state and state != "DISCONNECTED":
                    local_sdp = inv.get_active_local_sdp()
                    remote_sdp = inv.get_active_remote_sdp()
                    if not any(all(tup) for tup in zip([media.port for media in local_sdp.media], [media.port for media in remote_sdp.media])):
                        sdp_negotiated = False
                    if sdp_negotiated:
                        session._update_media(local_sdp, remote_sdp)
                    else:
                        inv.set_state_DISCONNECTED()
                if prev_state == "CALLING" and state == "EARLY":
                    session.state = "RINGING"
                elif state == "CONNECTING" and (session.state == "CALLING" or session.state == "RINGING"):
                    session.remote_user_agent = kwargs["headers"].get("Server", None)
                    if session.remote_user_agent is None:
                        session.remote_user_agent = kwargs["headers"].get("User-Agent", None)
                elif state == "CONFIRMED":
                    if prev_state == "CONFIRMED":
                        inv.respond_to_reinvite(488)
                    else:
                        session.state = "ESTABLISHED"
                elif state == "DISCONNECTED":
                    del self.session_mapping[inv]
                    session.state = "TERMINATED"
                    if "headers" in kwargs:
                        if session.remote_user_agent is None:
                            session.remote_user_agent = kwargs["headers"].get("Server", None)
                        if session.remote_user_agent is None:
                            session.remote_user_agent = kwargs["headers"].get("User-Agent", None)
                    session._stop_media()
                    session._inv = None
                session._lock.release()
                if prev_session_state != session.state:
                    kwargs["obj"] = session
                    kwargs["prev_state"] = prev_session_state
                    kwargs["state"] = session.state
                    self.event_handler("Session_state", **kwargs)
        else:
            self.event_handler(event_name, **kwargs)
