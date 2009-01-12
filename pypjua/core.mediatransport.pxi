# classes

cdef class RTPTransport:
    cdef pjmedia_transport *c_obj
    cdef pjmedia_transport *c_wrapped_transport
    cdef pj_pool_t *c_pool
    cdef readonly object remote_rtp_port_sdp
    cdef readonly object remote_rtp_address_sdp
    cdef readonly object state
    cdef readonly object use_srtp
    cdef readonly object srtp_forced
    cdef readonly object use_ice
    cdef readonly object ice_stun_address
    cdef readonly object ice_stun_port

    def __cinit__(self, local_rtp_address=None, use_srtp=False, srtp_forced=False, use_ice=False, ice_stun_address=None, ice_stun_port=PJ_STUN_PORT):
        global _RTPTransport_stun_list, _ice_cb
        cdef object pool_name = "RTPTransport_%d" % id(self)
        cdef char c_local_rtp_address[PJ_INET6_ADDRSTRLEN]
        cdef int af = pj_AF_INET()
        cdef pj_str_t c_local_ip
        cdef pj_str_t *c_local_ip_p = &c_local_ip
        cdef pjmedia_srtp_setting srtp_setting
        cdef pj_ice_strans_cfg ice_cfg
        cdef int i
        cdef int status
        cdef PJSIPUA ua = c_get_ua()
        self.state = "CINIT"
        self.use_srtp = use_srtp
        self.srtp_forced = srtp_forced
        self.use_ice = use_ice
        self.ice_stun_address = ice_stun_address
        self.ice_stun_port = ice_stun_port
        self.c_pool = pjsip_endpt_create_pool(ua.c_pjsip_endpoint.c_obj, pool_name, 4096, 4096)
        if self.c_pool == NULL:
            raise MemoryError()
        if local_rtp_address is None:
            c_local_ip_p = NULL
        else:
            if ":" in local_rtp_address:
                af = pj_AF_INET6()
            str_to_pj_str(local_rtp_address, &c_local_ip)
        if use_ice:
            pj_ice_strans_cfg_default(&ice_cfg)
            pj_stun_config_init(&ice_cfg.stun_cfg, &ua.c_caching_pool.c_obj.factory, 0, pjmedia_endpt_get_ioqueue(ua.c_pjmedia_endpoint.c_obj), pjsip_endpt_get_timer_heap(ua.c_pjsip_endpoint.c_obj))
            if ice_stun_address is not None:
                str_to_pj_str(ice_stun_address, &ice_cfg.stun.server)
                ice_cfg.stun.port = ice_stun_port
            status = pj_sockaddr_init(ice_cfg.af, &ice_cfg.stun.cfg.bound_addr, c_local_ip_p, 0)
            if status != 0:
                raise RuntimeError("Could not init ICE bound address: %s" % pj_status_to_str(status))
            status = pjmedia_ice_create2(ua.c_pjmedia_endpoint.c_obj, NULL, 2, &ice_cfg, &_ice_cb, 0, &self.c_obj)
            if status != 0:
                raise RuntimeError("Could not create ICE media transport: %s" % pj_status_to_str(status))
        else:
            status = PJ_EBUG
            for i in xrange(ua.c_rtp_port_index, ua.c_rtp_port_index + ua.c_rtp_port_stop - ua.c_rtp_port_start, 2):
                status = pjmedia_transport_udp_create3(ua.c_pjmedia_endpoint.c_obj, af, NULL, c_local_ip_p, ua.c_rtp_port_start + i % (ua.c_rtp_port_stop - ua.c_rtp_port_start), 0, &self.c_obj)
                if status != PJ_ERRNO_START_SYS + EADDRINUSE:
                    ua.c_rtp_port_index = (i + 2) % (ua.c_rtp_port_stop - ua.c_rtp_port_start)
                    break
            if status != 0:
                raise RuntimeError("Could not create UDP/RTP media transport: %s" % pj_status_to_str(status))
        if use_srtp:
            self.c_wrapped_transport = self.c_obj
            self.c_obj = NULL
            pjmedia_srtp_setting_default(&srtp_setting)
            if srtp_forced:
                srtp_setting.use = PJMEDIA_SRTP_MANDATORY
            status = pjmedia_transport_srtp_create(ua.c_pjmedia_endpoint.c_obj, self.c_wrapped_transport, &srtp_setting, &self.c_obj)
            if status != 0:
                raise RuntimeError("Could not create SRTP media transport: %s" % pj_status_to_str(status))
        if ice_stun_address is None:
            self.state = "INIT"
        else:
            _RTPTransport_stun_list.append(self)
            self.state = "WAIT_STUN"

    def __dealloc__(self):
        global _RTPTransport_stun_list
        cdef PJSIPUA ua
        try:
            ua = c_get_ua()
        except RuntimeError:
            return
        if self.state in ["LOCAL", "ESTABLISHED"]:
            pjmedia_transport_media_stop(self.c_obj)
        if self.c_obj != NULL:
            pjmedia_transport_close(self.c_obj)
            self.c_wrapped_transport = NULL
        if self.c_wrapped_transport != NULL:
            pjmedia_transport_close(self.c_wrapped_transport)
        if self.c_pool != NULL:
            pjsip_endpt_release_pool(ua.c_pjsip_endpoint.c_obj, self.c_pool)
        if self in _RTPTransport_stun_list:
            _RTPTransport_stun_list.remove(self)

    cdef int _get_info(self, pjmedia_transport_info *info) except -1:
        cdef int status
        pjmedia_transport_info_init(info)
        status = pjmedia_transport_get_info(self.c_obj, info)
        if status != 0:
            raise RuntimeError("Could not get transport info: %s" % pj_status_to_str(status))
        return 0

    property local_rtp_port:

        def __get__(self):
            cdef pjmedia_transport_info info
            if self.state in ["WAIT_STUN", "STUN_FAILED"]:
                return None
            self._get_info(&info)
            if info.sock_info.rtp_addr_name.addr.sa_family != 0:
                return pj_sockaddr_get_port(&info.sock_info.rtp_addr_name)
            else:
                return None

    property local_rtp_address:

        def __get__(self):
            cdef pjmedia_transport_info info
            cdef char buf[PJ_INET6_ADDRSTRLEN]
            if self.state in ["WAIT_STUN", "STUN_FAILED"]:
                return None
            self._get_info(&info)
            if pj_sockaddr_has_addr(&info.sock_info.rtp_addr_name):
                return pj_sockaddr_print(&info.sock_info.rtp_addr_name, buf, PJ_INET6_ADDRSTRLEN, 0)
            else:
                return None

    property remote_rtp_port_received:

        def __get__(self):
            cdef pjmedia_transport_info info
            if self.state in ["WAIT_STUN", "STUN_FAILED"]:
                return None
            self._get_info(&info)
            if info.src_rtp_name.addr.sa_family != 0:
                return pj_sockaddr_get_port(&info.src_rtp_name)
            else:
                return None

    property remote_rtp_address_received:

        def __get__(self):
            cdef pjmedia_transport_info info
            cdef char buf[PJ_INET6_ADDRSTRLEN]
            if self.state in ["WAIT_STUN", "STUN_FAILED"]:
                return None
            self._get_info(&info)
            if pj_sockaddr_has_addr(&info.src_rtp_name):
                return pj_sockaddr_print(&info.src_rtp_name, buf, PJ_INET6_ADDRSTRLEN, 0)
            else:
                return None

    property srtp_active:

        def __get__(self):
            cdef pjmedia_transport_info info
            cdef pjmedia_srtp_info *srtp_info
            cdef int i
            if self.state in ["WAIT_STUN", "STUN_FAILED"]:
                return False
            self._get_info(&info)
            for i from 0 <= i < info.specific_info_cnt:
                if info.spc_info[i].type == PJMEDIA_TRANSPORT_TYPE_SRTP:
                    srtp_info = <pjmedia_srtp_info *> info.spc_info[i].buffer
                    return bool(srtp_info.active)
            return False

    cdef int _update_local_sdp(self, SDPSession local_sdp, unsigned int sdp_index, pjmedia_sdp_session *c_remote_sdp) except -1:
        cdef int status
        status = pjmedia_transport_media_create(self.c_obj, self.c_pool, 0, c_remote_sdp, sdp_index)
        if status != 0:
            raise RuntimeError("Could not create media transport: %s" % pj_status_to_str(status))
        status = pjmedia_transport_encode_sdp(self.c_obj, self.c_pool, &local_sdp.c_obj, c_remote_sdp, sdp_index)
        if status != 0:
            raise RuntimeError("Could not update SDP for media transport: %s" % pj_status_to_str(status))
        # TODO: work the changes back into the local_sdp object, but we don't need to do that yet.
        return 0

    def set_LOCAL(self, SDPSession local_sdp, unsigned int sdp_index):
        if local_sdp is None:
            raise RuntimeError("local_sdp argument cannot be None")
        if self.state == "LOCAL":
            return
        if self.state != "INIT":
            raise RuntimeError('set_LOCAL can only be called in the "INIT" state')
        local_sdp._to_c()
        self._update_local_sdp(local_sdp, sdp_index, NULL)
        self.state = "LOCAL"

    def set_ESTABLISHED(self, SDPSession local_sdp, SDPSession remote_sdp, unsigned int sdp_index):
        cdef int status
        cdef PJSIPUA = c_get_ua()
        if None in [local_sdp, remote_sdp]:
            raise RuntimeError("SDP arguments cannot be None")
        if self.state == "ESTABLISHED":
            return
        if self.state not in ["INIT", "LOCAL"]:
            raise RuntimeError('set_ESTABLISHED can only be called in the "INIT" and "LOCAL" states')
        local_sdp._to_c()
        remote_sdp._to_c()
        if self.state == "INIT":
            self._update_local_sdp(local_sdp, sdp_index, &remote_sdp.c_obj)
        status = pjmedia_transport_media_start(self.c_obj, self.c_pool, &local_sdp.c_obj, &remote_sdp.c_obj, sdp_index)
        if status != 0:
            raise RuntimeError("Could not start media transport: %s" % pj_status_to_str(status))
        if remote_sdp.media[sdp_index].connection is None:
            if remote_sdp.connection is not None:
                self.remote_rtp_address_sdp = remote_sdp.connection.address
        else:
            self.remote_rtp_address_sdp = remote_sdp.media[sdp_index].connection.address
        self.remote_rtp_port_sdp = remote_sdp.media[sdp_index].port
        self.state = "ESTABLISHED"

    def set_INIT(self):
        cdef int status
        if self.state == "INIT":
            return
        if self.state not in ["LOCAL", "ESTABLISHED"]:
            raise RuntimeError('set_INIT can only be called in the "LOCAL" and "ESTABLISHED" states')
        status = pjmedia_transport_media_stop(self.c_obj)
        if status != 0:
            raise RuntimeError("Could not stop media transport: %s" % pj_status_to_str(status))
        self.remote_rtp_address_sdp = None
        self.remote_rtp_port_sdp = None
        self.state = "INIT"

cdef class AudioTransport:
    cdef pjmedia_stream *c_obj
    cdef pjmedia_stream_info c_stream_info
    cdef readonly RTPTransport transport
    cdef pj_pool_t *c_pool
    cdef pjmedia_sdp_media *c_local_media
    cdef unsigned int c_conf_slot
    cdef readonly object direction
    cdef int c_started
    cdef int c_offer

    def __cinit__(self, RTPTransport transport, SDPSession remote_sdp = None, unsigned int sdp_index = 0):
        cdef object pool_name = "AudioTransport_%d" % id(self)
        cdef pjmedia_transport_info info
        cdef pjmedia_sdp_session *c_local_sdp
        cdef SDPSession local_sdp
        cdef int status
        cdef PJSIPUA ua = c_get_ua()
        if transport is None:
            raise RuntimeError("transport argument cannot be None")
        if transport.state != "INIT":
            raise RuntimeError('RTPTransport object provided is not in the "INIT" state')
        self.transport = transport
        self.c_started = 0
        self.c_pool = pjsip_endpt_create_pool(ua.c_pjsip_endpoint.c_obj, pool_name, 4096, 4096)
        if self.c_pool == NULL:
            raise MemoryError()
        transport._get_info(&info)
        status = pjmedia_endpt_create_sdp(ua.c_pjmedia_endpoint.c_obj, self.c_pool, 1, &info.sock_info, &c_local_sdp)
        if status != 0:
            raise RuntimeError("Could not generate SDP for audio session: %s" % pj_status_to_str(status))
        local_sdp = c_make_SDPSession(c_local_sdp)
        if remote_sdp is None:
            self.c_offer = 1
            self.transport.set_LOCAL(local_sdp, 0)
        else:
            self.c_offer = 0
            if sdp_index != 0:
                local_sdp.media = (sdp_index+1) * local_sdp.media
            self.transport.set_ESTABLISHED(local_sdp, remote_sdp, sdp_index)
        self.c_local_media = pjmedia_sdp_media_clone(self.c_pool, local_sdp.c_obj.media[sdp_index])

    def __dealloc__(self):
        cdef PJSIPUA ua
        try:
            ua = c_get_ua()
        except RuntimeError:
            return
        if self.c_obj != NULL:
            self.stop()
        if self.c_pool != NULL:
            pjsip_endpt_release_pool(ua.c_pjsip_endpoint.c_obj, self.c_pool)

    property is_active:

        def __get__(self):
            return bool(self.c_obj != NULL)

    property is_started:

        def __get__(self):
            return bool(self.c_started)

    property codec:

        def __get__(self):
            if self.c_obj == NULL:
                return None
            else:
                return pj_str_to_str(self.c_stream_info.fmt.encoding_name)

    property sample_rate:

        def __get__(self):
            if self.c_obj == NULL:
                return None
            else:
                return self.c_stream_info.fmt.clock_rate

    def get_local_media(self, is_offer, direction="sendrecv"):
        cdef SDPAttribute attr
        cdef SDPMedia local_media
        if direction not in ["sendrecv", "sendonly", "recvonly", "inactive"]:
            raise RuntimeError("Unknown direction: %s" % direction)
        local_media = c_make_SDPMedia(self.c_local_media)
        local_media.attributes = [<object> attr for attr in local_media.attributes if attr.name not in ["sendrecv", "sendonly", "recvonly", "inactive"]]
        if is_offer and direction != "sendrecv":
            local_media.attributes.append(SDPAttribute(direction, ""))
        return local_media

    def start(self, SDPSession local_sdp, SDPSession remote_sdp, unsigned int sdp_index):
        cdef pjmedia_port *media_port
        cdef int status
        cdef PJSIPUA ua = c_get_ua()
        if self.c_started:
            raise RuntimeError("This AudioTransport was already started once")
        if self.c_offer and self.transport.state != "LOCAL" or not self.c_offer and self.transport.state != "ESTABLISHED":
            raise RuntimeError("RTPTransport object provided is in wrong state")
        if None in [local_sdp, remote_sdp]:
            raise RuntimeError("SDP arguments cannot be None")
        if local_sdp.media[sdp_index].port == 0 or remote_sdp.media[sdp_index].port == 0:
            raise RuntimeError("Cannot start a rejected audio stream")
        if self.transport.state == "LOCAL":
            self.transport.set_ESTABLISHED(local_sdp, remote_sdp, sdp_index)
        else:
            local_sdp._to_c()
            remote_sdp._to_c()
        status = pjmedia_stream_info_from_sdp(&self.c_stream_info, self.c_pool, ua.c_pjmedia_endpoint.c_obj, &local_sdp.c_obj, &remote_sdp.c_obj, sdp_index)
        if status != 0:
            raise RuntimeError("Could not parse SDP for audio session: %s" % pj_status_to_str(status))
        status = pjmedia_stream_create(ua.c_pjmedia_endpoint.c_obj, self.c_pool, &self.c_stream_info, self.transport.c_obj, NULL, &self.c_obj)
        if status != 0:
            raise RuntimeError("Could not initialize RTP for audio session: %s" % pj_status_to_str(status))
        status = pjmedia_stream_set_dtmf_callback(self.c_obj, cb_AudioTransport_cb_dtmf, <void *> self)
        if status != 0:
            pjmedia_stream_destroy(self.c_obj)
            self.c_obj = NULL
            raise RuntimeError("Could not set DTMF callback for audio session: %s" % pj_status_to_str(status))
        status = pjmedia_stream_start(self.c_obj)
        if status != 0:
            pjmedia_stream_destroy(self.c_obj)
            self.c_obj = NULL
            raise RuntimeError("Could not start RTP for audio session: %s" % pj_status_to_str(status))
        status = pjmedia_stream_get_port(self.c_obj, &media_port)
        if status != 0:
            pjmedia_stream_destroy(self.c_obj)
            self.c_obj = NULL
            raise RuntimeError("Could not get audio port for audio session: %s" % pj_status_to_str(status))
        status = pjmedia_conf_add_port(ua.c_conf_bridge.c_obj, self.c_pool, media_port, NULL, &self.c_conf_slot)
        if status != 0:
            pjmedia_stream_destroy(self.c_obj)
            self.c_obj = NULL
            raise RuntimeError("Could not connect audio session to conference bridge: %s" % pj_status_to_str(status))
        self.direction = "sendrecv"
        self.update_direction(local_sdp.media[sdp_index].get_direction())
        self.c_local_media = pjmedia_sdp_media_clone(self.c_pool, local_sdp.c_obj.media[sdp_index])
        self.c_started = 1

    def stop(self):
        cdef PJSIPUA ua = c_get_ua()
        if self.c_obj == NULL:
            raise RuntimeError("Stream is not active")
        ua.c_conf_bridge._disconnect_slot(self.c_conf_slot)
        pjmedia_conf_remove_port(ua.c_conf_bridge.c_obj, self.c_conf_slot)
        pjmedia_stream_destroy(self.c_obj)
        self.c_obj = NULL
        self.transport.set_INIT()

    def update_direction(self, direction):
        cdef int status1 = 0
        cdef int status2 = 0
        if self.c_obj == NULL:
            raise RuntimeError("Stream is not active")
        if direction not in ["sendrecv", "sendonly", "recvonly", "inactive"]:
            raise RuntimeError("Unknown direction: %s" % direction)
        if direction == self.direction:
            return
        if "send" in self.direction:
            if "send" not in direction:
                status1 = pjmedia_stream_pause(self.c_obj, PJMEDIA_DIR_ENCODING)
        else:
            if "send" in direction:
                status1 = pjmedia_stream_resume(self.c_obj, PJMEDIA_DIR_ENCODING)
        if "recv" in self.direction:
            if "recv" not in direction:
                status2 = pjmedia_stream_pause(self.c_obj, PJMEDIA_DIR_DECODING)
        else:
            if "recv" in direction:
                status2 = pjmedia_stream_resume(self.c_obj, PJMEDIA_DIR_DECODING)
        self.direction = direction
        if status1 != 0:
            raise RuntimeError("Could not pause or resume encoding: %s" % pj_status_to_str(status1))
        if status2 != 0:
            raise RuntimeError("Could not pause or resume decoding: %s" % pj_status_to_str(status2))

    def send_dtmf(self, digit):
        cdef pj_str_t c_digit
        cdef int status
        cdef PJSIPUA ua = c_get_ua()
        if self.c_obj == NULL:
            raise RuntimeError("Stream is not active")
        if len(digit) != 1 or digit not in "0123456789*#ABCD":
            raise RuntimeError("Not a valid DTMF digit: %s" % digit)
        str_to_pj_str(digit, &c_digit)
        status = pjmedia_stream_dial_dtmf(self.c_obj, &c_digit)
        if status != 0:
            raise RuntimeError("Could not send DTMF digit on audio stream: %s" % pj_status_to_str(status))
        ua.c_conf_bridge._playback_dtmf(ord(digit))

# callback functions

cdef void cb_RTPTransport_ice_complete(pjmedia_transport *tp, pj_ice_strans_op op, int status) with gil:
    global _RTPTransport_stun_list
    cdef RTPTransport rtp_transport
    for rtp_transport in _RTPTransport_stun_list:
        if rtp_transport.c_obj == tp and op == PJ_ICE_STRANS_OP_INIT:
            if status == 0:
                rtp_transport.state = "INIT"
            else:
                rtp_transport.state = "STUN_FAILED"
            c_add_event("RTPTransport_init", dict(obj=rtp_transport, succeeded=status==0, status=pj_status_to_str(status)))
            _RTPTransport_stun_list.remove(rtp_transport)
            return

cdef void cb_AudioTransport_cb_dtmf(pjmedia_stream *stream, void *user_data, int digit) with gil:
    cdef AudioTransport audio_stream = <object> user_data
    cdef PJSIPUA ua = c_get_ua()
    c_add_event("AudioTransport_dtmf", dict(obj=audio_stream, digit=chr(digit)))
    ua.c_conf_bridge._playback_dtmf(digit)

# globals

cdef pjmedia_ice_cb _ice_cb
_ice_cb.on_ice_complete = cb_RTPTransport_ice_complete
_RTPTransport_stun_list = []