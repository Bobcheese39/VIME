" autoload/vime/state.vim - Shared state container for VIME modules
" Holds variables that need to be accessed across multiple autoload modules.

scriptencoding utf-8

let s:state = {
    \ 'current_file': '',
    \ 'plugin_dir': '',
    \ }

function! vime#state#get(key) abort
    return get(s:state, a:key, '')
endfunction

function! vime#state#set(key, val) abort
    let s:state[a:key] = a:val
endfunction
