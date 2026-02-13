" autoload/vime/http.vim - HTTP communication layer for VIME
" Handles all server communication via curl.

scriptencoding utf-8

" ======================================================================
" Public functions
" ======================================================================

function! vime#http#start_server() abort
    if vime#http#ping()
        return 1
    endif
    echoerr 'VIME: HTTP server not running. Start it with the vime wrapper.'
    return 0
endfunction

function! vime#http#stop_server() abort
    try
        call vime#http#send({'cmd': 'shutdown'})
    catch
    endtry
endfunction

function! vime#http#send(payload) abort
    if !exists('*json_encode') || !exists('*json_decode')
        echoerr 'VIME: Vim JSON support not available'
        return v:null
    endif
    let l:cmd = get(a:payload, 'cmd', '')
    if l:cmd ==# ''
        echoerr 'VIME: Missing command for request'
        return v:null
    endif
    let l:url = s:build_url('/' . l:cmd)
    let l:body = json_encode(a:payload)
    let l:resp = s:curl_post(l:url, l:body)
    if v:shell_error != 0
        echoerr 'VIME: HTTP request failed'
        return v:null
    endif
    try
        return json_decode(l:resp)
    catch
        echoerr 'VIME: Failed to decode server response'
        return v:null
    endtry
endfunction

function! vime#http#ping() abort
    let l:curl = get(g:, 'vime_curl_cmd', 'curl')
    let l:url = s:build_url('/health')
    let l:cmd = l:curl . ' -sS ' . shellescape(l:url)
    let l:resp = system(l:cmd)
    if v:shell_error != 0 || l:resp ==# ''
        return 0
    endif
    if exists('*json_decode')
        try
            let l:obj = json_decode(l:resp)
            return type(l:obj) == v:t_dict && get(l:obj, 'ok', 0)
        catch
            return 0
        endtry
    endif
    return 1
endfunction

function! vime#http#check_response(resp, context) abort
    if type(a:resp) != v:t_dict
        echoerr 'VIME: ' . a:context . ' (server returned unexpected response)'
        return 0
    endif
    if !get(a:resp, 'ok', 0)
        echoerr 'VIME: ' . get(a:resp, 'error', a:context)
        return 0
    endif
    return 1
endfunction

" ======================================================================
" Private helpers
" ======================================================================

function! s:build_url(path) abort
    let l:host = get(g:, 'vime_http_host', '127.0.0.1')
    let l:port = get(g:, 'vime_http_port', 51789)
    return 'http://' . l:host . ':' . l:port . a:path
endfunction

function! s:curl_post(url, body) abort
    let l:curl = get(g:, 'vime_curl_cmd', 'curl')
    let l:cmd = l:curl
        \ . ' -sS -X POST -H "Content-Type: application/json"'
        \ . ' -d ' . shellescape(a:body)
        \ . ' ' . shellescape(a:url)
    return system(l:cmd)
endfunction
