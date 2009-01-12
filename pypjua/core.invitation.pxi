# main class

cdef class Invitation:
    cdef pjsip_inv_session *c_obj
    cdef pjsip_dialog *c_dlg
    cdef Credentials c_credentials
    cdef SIPURI c_caller_uri
    cdef SIPURI c_callee_uri
    cdef Route c_route
    cdef readonly object state
    cdef readonly object sdp_state
    cdef int c_is_ending
    cdef SDPSession c_local_sdp_proposed
    cdef int c_sdp_neg_status
    cdef int c_has_active_sdp

    def __cinit__(self, *args, route=None):
        cdef PJSIPUA ua = c_get_ua()
        self.state = "NULL"
        self.sdp_state = "NULL"
        self.c_is_ending = 0
        self.c_sdp_neg_status = -1
        self.c_has_active_sdp = 0
        if len(args) != 0:
            if None in args:
                raise TypeError("Positional arguments cannot be None")
            try:
                self.c_credentials, self.c_callee_uri = args
            except ValueError:
                raise TypeError("Expected 2 positional arguments")
            if self.c_credentials.uri is None:
                raise RuntimeError("No SIP URI set on credentials")
            self.c_credentials = self.c_credentials.copy()
            self.c_credentials._to_c()
            self.c_caller_uri = self.c_credentials.uri
            if route is not None:
                self.c_route = route.copy()
                self.c_route._to_c(ua)

    cdef int _init_incoming(self, PJSIPUA ua, pjsip_rx_data *rdata, unsigned int inv_options) except -1:
        cdef pjsip_tx_data *tdata
        cdef char contact_uri_buf[1024]
        cdef pj_str_t contact_uri
        cdef unsigned int i
        cdef int status
        contact_uri.ptr = contact_uri_buf
        try:
            status = pjsip_uri_print(PJSIP_URI_IN_CONTACT_HDR, rdata.msg_info.msg.line.req.uri, contact_uri_buf, 1024)
            if status == -1:
                raise RuntimeError("Request URI is too long")
            contact_uri.slen = status
            status = pjsip_dlg_create_uas(pjsip_ua_instance(), rdata, &contact_uri, &self.c_dlg)
            if status != 0:
                raise RuntimeError("Could not create dialog for new INTIVE session: %s" % pj_status_to_str(status))
            status = pjsip_inv_create_uas(self.c_dlg, rdata, NULL, inv_options, &self.c_obj)
            if status != 0:
                raise RuntimeError("Could not create new INTIVE session: %s" % pj_status_to_str(status))
            status = pjsip_inv_initial_answer(self.c_obj, rdata, 100, NULL, NULL, &tdata)
            if status != 0:
                raise RuntimeError("Could not create initial (unused) response to INTIVE: %s" % pj_status_to_str(status))
            pjsip_tx_data_dec_ref(tdata)
            self.c_obj.mod_data[ua.c_module.id] = <void *> self
            self._cb_state(rdata, PJSIP_INV_STATE_INCOMING)
        except:
            if self.c_obj != NULL:
                pjsip_inv_terminate(self.c_obj, 500, 0)
            elif self.c_dlg != NULL:
                pjsip_dlg_terminate(self.c_dlg)
            self.c_obj = NULL
            self.c_dlg = NULL
            raise
        self.c_caller_uri = c_make_SIPURI(rdata.msg_info.from_hdr.uri, 1)
        self.c_callee_uri = c_make_SIPURI(rdata.msg_info.to_hdr.uri, 1)
        return 0

    def __dealloc__(self):
        cdef PJSIPUA ua
        try:
            ua = c_get_ua()
        except RuntimeError:
            return
        if self.c_obj != NULL:
            self.c_obj.mod_data[ua.c_module.id] = NULL
            if self.c_obj != NULL and not self.c_is_ending:
                pjsip_inv_terminate(self.c_obj, 481, 0)

    property caller_uri:

        def __get__(self):
            return self.c_caller_uri.copy()

    property callee_uri:

        def __get__(self):
            return self.c_callee_uri.copy()

    property credentials:

        def __get__(self):
            return self.c_credentials.copy()

    property route:

        def __get__(self):
            return self.c_route.copy()

    def get_active_local_sdp(self):
        cdef pjmedia_sdp_session_ptr_const sdp
        if self.c_obj != NULL and self.c_has_active_sdp:
            pjmedia_sdp_neg_get_active_local(self.c_obj.neg, &sdp)
            return c_make_SDPSession(sdp)
        else:
            return None

    def get_active_remote_sdp(self):
        cdef pjmedia_sdp_session_ptr_const sdp
        if self.c_obj != NULL and self.c_has_active_sdp:
            pjmedia_sdp_neg_get_active_remote(self.c_obj.neg, &sdp)
            return c_make_SDPSession(sdp)
        else:
            return None

    def get_offered_remote_sdp(self):
        cdef pjmedia_sdp_session_ptr_const sdp
        if self.c_obj != NULL and self.sdp_state == "REMOTE_OFFER":
            pjmedia_sdp_neg_get_neg_remote(self.c_obj.neg, &sdp)
            return c_make_SDPSession(sdp)
        else:
            return None

    def get_offered_local_sdp(self):
        cdef pjmedia_sdp_session_ptr_const sdp
        if self.c_obj != NULL and self.sdp_state == "LOCAL_OFFER":
            pjmedia_sdp_neg_get_neg_local(self.c_obj.neg, &sdp)
            return c_make_SDPSession(sdp)
        else:
            return self.c_local_sdp_proposed

    def set_offered_local_sdp(self, local_sdp):
        if self.state == "DISCONNECTED":
            raise RuntimeError("Session was already disconnected")
        if self.sdp_state == "LOCAL_OFFER":
            raise RuntimeError("Local SDP is already being proposed")
        else:
            self.c_local_sdp_proposed = local_sdp

    cdef int _cb_state(self, pjsip_rx_data *rdata, pjsip_inv_state state) except -1:
        cdef pjsip_tx_data *tdata
        cdef int status
        cdef PJSIPUA ua = c_get_ua()
        cdef dict event_dict = dict(obj=self, prev_state=self.state, prev_sdp_state=self.sdp_state)
        self.state = pjsip_inv_state_name(state)
        if rdata != NULL:
            c_rdata_info_to_dict(rdata, event_dict)
        self.sdp_state = event_dict["sdp_state"] = pjmedia_sdp_neg_state_str(pjmedia_sdp_neg_get_state(self.c_obj.neg)).split("STATE_", 1)[1]
        if self.state == "DISCONNCTD":
            self.state = "DISCONNECTED"
            if rdata == NULL and self.c_obj.cause > 0:
                event_dict["code"] = self.c_obj.cause
                event_dict["reason"] = pj_str_to_str(self.c_obj.cause_text)
            self.c_obj.mod_data[ua.c_module.id] = NULL
            self.c_obj = NULL
        event_dict["state"] = self.state
        if self.sdp_state == "DONE" and event_dict["prev_sdp_state"] != "DONE":
            event_dict["sdp_negotiated"] = not bool(self.c_sdp_neg_status)
            self.c_local_sdp_proposed = None
        if self.state == "CONFIRMED" and self.sdp_state == "REMOTE_OFFER":
            status = pjsip_inv_initial_answer(self.c_obj, rdata, 100, NULL, NULL, &tdata)
            if status != 0:
                raise RuntimeError("Could not create initial (unused) response to INTIVE: %s" % pj_status_to_str(status))
            pjsip_tx_data_dec_ref(tdata)
        if event_dict["prev_state"] != self.state or event_dict["prev_sdp_state"] != self.sdp_state:
            c_add_event("Invitation_state", event_dict)
        return 0

    cdef int _cb_sdp_done(self, int status) except -1:
        self.c_sdp_neg_status = status
        if status == 0:
            self.c_has_active_sdp = 1
        if self.state == "CONFIRMED" and self.sdp_state == "REMOTE_OFFER":
                self._cb_state(NULL, PJSIP_INV_STATE_CONFIRMED)
        return 0

    cdef int _send_msg(self, PJSIPUA ua, pjsip_tx_data *tdata, dict extra_headers) except -1:
        cdef int status
        cdef object name, value
        cdef GenericStringHeader header
        cdef list c_extra_headers = [GenericStringHeader(name, value) for name, value in extra_headers.iteritems()]
        pjsip_msg_add_hdr(tdata.msg, <pjsip_hdr *> pjsip_hdr_clone(tdata.pool, &ua.c_user_agent_hdr.c_obj))
        for header in c_extra_headers:
            pjsip_msg_add_hdr(tdata.msg, <pjsip_hdr *> pjsip_hdr_clone(tdata.pool, &header.c_obj))
        status = pjsip_inv_send_msg(self.c_obj, tdata)
        if status != 0:
            pjsip_tx_data_dec_ref(tdata)
            raise RuntimeError("Could not send message in context of INVITE session: %s" % pj_status_to_str(status))
        return 0

    def set_state_CALLING(self, dict extra_headers=None):
        cdef pjsip_tx_data *tdata
        cdef object transport
        cdef PJSTR caller_uri
        cdef PJSTR callee_uri
        cdef PJSTR callee_target
        cdef PJSTR contact_uri
        cdef pjmedia_sdp_session *local_sdp = NULL
        cdef int status
        cdef PJSIPUA ua = c_get_ua()
        if self.state != "NULL":
            raise RuntimeError("Can only transition to the CALLING state from the NULL state")
        caller_uri = PJSTR(self.c_caller_uri._as_str(0))
        callee_uri = PJSTR(self.c_callee_uri._as_str(0))
        callee_target = PJSTR(self.c_callee_uri._as_str(1))
        if self.c_route is not None:
            transport = self.c_route.transport
        contact_uri = ua.c_create_contact_uri(self.c_credentials.token, transport)
        try:
            status = pjsip_dlg_create_uac(pjsip_ua_instance(), &caller_uri.pj_str, &contact_uri.pj_str, &callee_uri.pj_str, &callee_target.pj_str, &self.c_dlg)
            if status != 0:
                raise RuntimeError("Could not create dialog for outgoing INVITE session: %s" % pj_status_to_str(status))
            if self.c_local_sdp_proposed is not None:
                self.c_local_sdp_proposed._to_c()
                local_sdp = &self.c_local_sdp_proposed.c_obj
            status = pjsip_inv_create_uac(self.c_dlg, local_sdp, 0, &self.c_obj)
            if status != 0:
                raise RuntimeError("Could not create outgoing INVITE session: %s" % pj_status_to_str(status))
            self.c_obj.mod_data[ua.c_module.id] = <void *> self
            status = pjsip_auth_clt_set_credentials(&self.c_dlg.auth_sess, 1, &self.c_credentials.c_obj)
            if status != 0:
                raise RuntimeError("Could not set credentials for INVITE session: %s" % pj_status_to_str(status))
            if self.c_route is not None:
                status = pjsip_dlg_set_route_set(self.c_dlg, &self.c_route.c_route_set)
                if status != 0:
                    raise RuntimeError("Could not set route for INVITE session: %s" % pj_status_to_str(status))
            status = pjsip_inv_invite(self.c_obj, &tdata)
            if status != 0:
                raise RuntimeError("Could not create INVITE message: %s" % pj_status_to_str(status))
            self._send_msg(ua, tdata, extra_headers or {})
        except:
            if self.c_obj != NULL:
                pjsip_inv_terminate(self.c_obj, 500, 0)
                self.c_obj = NULL
            elif self.c_dlg != NULL:
                pjsip_dlg_terminate(self.c_dlg)
                self.c_dlg = NULL
            raise

    def set_state_EARLY(self, int reply_code=180, dict extra_headers=None):
        if self.state != "INCOMING":
            raise RuntimeError("Can only transition to the EARLY state from the INCOMING state")
        self._send_provisional_response(reply_code, extra_headers)

    cdef int _send_provisional_response(self, int reply_code, dict extra_headers) except -1:
        cdef pjsip_tx_data *tdata
        cdef int status
        cdef PJSIPUA ua = c_get_ua()
        if reply_code / 100 != 1:
            raise RuntimeError("Not a provisional response: %d" % reply_code)
        status = pjsip_inv_answer(self.c_obj, reply_code, NULL, NULL, &tdata)
        if status != 0:
            raise RuntimeError("Could not create %d reply to INVITE: %s" % (reply_code, pj_status_to_str(status)))
        self._send_msg(ua, tdata, extra_headers or {})
        return 0

    def set_state_CONNECTING(self, dict extra_headers=None):
        if self.state not in ["INCOMING", "EARLY"]:
            raise RuntimeError("Can only transition to the EARLY state from the INCOMING or EARLY states")
        self._send_response(extra_headers)

    cdef int _send_response(self, dict extra_headers) except -1:
        cdef pjsip_tx_data *tdata
        cdef int status
        cdef PJSIPUA ua = c_get_ua()
        if self.c_local_sdp_proposed is None:
            raise RuntimeError("Local SDP has not been set")
        self.c_local_sdp_proposed._to_c()
        status = pjsip_inv_answer(self.c_obj, 200, NULL, &self.c_local_sdp_proposed.c_obj, &tdata)
        if status != 0:
            raise RuntimeError("Could not create 200 reply to INVITE: %s" % pj_status_to_str(status))
        self._send_msg(ua, tdata, extra_headers or {})
        return 0

    def set_state_DISCONNECTED(self, int reply_code=486, dict extra_headers=None):
        cdef pjsip_tx_data *tdata
        cdef int status
        cdef PJSIPUA ua = c_get_ua()
        if self.c_obj == NULL:
            raise RuntimeError("INVITE session is not active")
        if reply_code / 100 < 3:
            raise RuntimeError("Not a non-2xx final response: %d" % reply_code)
        if self.state == "INCOMING":
            status = pjsip_inv_answer(self.c_obj, reply_code, NULL, NULL, &tdata)
        else:
            status = pjsip_inv_end_session(self.c_obj, reply_code, NULL, &tdata)
        if status != 0:
            raise RuntimeError("Could not create message to end INVITE session: %s" % pj_status_to_str(status))
        if tdata != NULL:
            self._send_msg(ua, tdata, extra_headers or {})

    def respond_to_reinvite_provisionally(self, int reply_code=180, dict extra_headers=None):
        if self.state != "CONFIRMED" or self.sdp_state != "REMOTE_OFFER":
            raise RuntimeError("Can only send a provisional repsonse to a re-INVITE when we have received one")
        self._send_provisional_response(reply_code, extra_headers)

    def respond_to_reinvite(self, dict extra_headers=None):
        if self.state != "CONFIRMED" or self.sdp_state != "REMOTE_OFFER":
            raise RuntimeError("Can only send a repsonse to a re-INVITE when we have received one")
        self._send_response(extra_headers)

    def send_reinvite(self, dict extra_headers=None):
        cdef pjsip_tx_data *tdata
        cdef object sdp_state
        cdef int status
        cdef pjmedia_sdp_session *local_sdp = NULL
        cdef PJSIPUA ua = c_get_ua()
        if self.state != "CONFIRMED":
            raise RuntimeError("Cannot send re-INVITE in CONFIRMED state")
        if self.c_local_sdp_proposed is not None:
            self.c_local_sdp_proposed._to_c()
            local_sdp = &self.c_local_sdp_proposed.c_obj
        status = pjsip_inv_reinvite(self.c_obj, NULL, local_sdp, &tdata)
        if status != 0:
            raise RuntimeError("Could not create re-INVITE message: %s" % pj_status_to_str(status))
        self._send_msg(ua, tdata, extra_headers or {})
        if self.c_local_sdp_proposed is not None:
            self._cb_state(NULL, self.c_obj.state)

# callback functions

cdef void cb_Invitation_cb_state(pjsip_inv_session *inv, pjsip_event *e) with gil:
    cdef Invitation invitation
    cdef pjsip_rx_data *rdata = NULL
    cdef PJSIPUA ua = c_get_ua()
    if _ua != NULL:
        ua = <object> _ua
        if inv.state == PJSIP_INV_STATE_INCOMING:
            return
        if inv.mod_data[ua.c_module.id] != NULL:
            invitation = <object> inv.mod_data[ua.c_module.id]
            if e != NULL:
                if e.type == PJSIP_EVENT_RX_MSG:
                    rdata = e.body.rx_msg.rdata
                elif e.type == PJSIP_EVENT_TSX_STATE and e.body.tsx_state.type == PJSIP_EVENT_RX_MSG:
                    if inv.state != PJSIP_INV_STATE_CONFIRMED or e.body.tsx_state.src.rdata.msg_info.msg.type == PJSIP_REQUEST_MSG:
                        rdata = e.body.tsx_state.src.rdata
            invitation._cb_state(rdata, inv.state)

cdef void cb_Invitation_cb_sdp_done(pjsip_inv_session *inv, int status) with gil:
    cdef Invitation invitation
    cdef PJSIPUA ua = c_get_ua()
    if _ua != NULL:
        ua = <object> _ua
        if inv.mod_data[ua.c_module.id] != NULL:
            invitation = <object> inv.mod_data[ua.c_module.id]
            invitation._cb_sdp_done(status)

cdef void cb_Invitation_cb_rx_reinvite(pjsip_inv_session *inv, pjmedia_sdp_session_ptr_const offer, pjsip_rx_data *rdata) with gil:
    cdef Invitation invitation
    cdef PJSIPUA ua = c_get_ua()
    if _ua != NULL:
        ua = <object> _ua
        if inv.mod_data[ua.c_module.id] != NULL:
            invitation = <object> inv.mod_data[ua.c_module.id]
            invitation._cb_state(rdata, inv.state)

cdef void cb_Invitation_cb_tsx_state_changed(pjsip_inv_session *inv, pjsip_transaction *tsx, pjsip_event *e) with gil:
    cdef Invitation invitation
    cdef pjsip_rx_data *rdata = NULL
    cdef PJSIPUA ua = c_get_ua()
    if _ua != NULL:
        ua = <object> _ua
        if inv.mod_data[ua.c_module.id] != NULL:
            invitation = <object> inv.mod_data[ua.c_module.id]
            if invitation.state != "CONFIRMED" or invitation.sdp_state != "LOCAL_OFFER":
                return
            if e != NULL:
                if e.type == PJSIP_EVENT_RX_MSG:
                    rdata = e.body.rx_msg.rdata
                elif e.type == PJSIP_EVENT_TSX_STATE and e.body.tsx_state.type == PJSIP_EVENT_RX_MSG:
                    rdata = e.body.tsx_state.src.rdata
            if rdata != NULL:
                invitation._cb_state(rdata, PJSIP_INV_STATE_CONFIRMED)

cdef void cb_new_Invitation(pjsip_inv_session *inv, pjsip_event *e) with gil:
    # As far as I can tell this is never actually called!
    pass

# globals

cdef pjsip_inv_callback _inv_cb
_inv_cb.on_state_changed = cb_Invitation_cb_state
_inv_cb.on_media_update = cb_Invitation_cb_sdp_done
_inv_cb.on_rx_reinvite = cb_Invitation_cb_rx_reinvite
_inv_cb.on_tsx_state_changed = cb_Invitation_cb_tsx_state_changed
_inv_cb.on_new_session = cb_new_Invitation