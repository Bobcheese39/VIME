" vime.vim - Vim H5 file viewer plugin
" Requires curl and a Vim build with JSON support
"
" Usage: vim file.h5   -> opens table list
"        :VimeOpen <file>   -> open an H5 file
"        :VimePlot <col1> <col2> [type]  -> plot columns
"        :VimeInfo          -> info about current table

scriptencoding utf-8

if exists('g:loaded_vime')
    finish
endif
let g:loaded_vime = 1

" Store the plugin directory (must be done here; <sfile> only works at source time)
call vime#state#set('plugin_dir', fnamemodify(expand('<sfile>:p'), ':h'))

" Initialize Nord highlight groups
call vime#colors#define()

" ======================================================================
" Commands
" ======================================================================

command! -nargs=1 -complete=file VimeOpen call vime#list#open(fnamemodify(<q-args>, ':p'))

command! -nargs=+ VimePlot call s:VimePlotCmd(<f-args>)
function! s:VimePlotCmd(...) abort
    if a:0 < 2
        echoerr 'Usage: :VimePlot <col1> <col2> [scatter|line]'
        return
    endif
    let l:ptype = a:0 >= 3 ? a:3 : 'line'
    call vime#plot#do_plot(a:1, a:2, l:ptype)
endfunction

command! -nargs=0 VimeInfo call s:VimeInfoCmd()
function! s:VimeInfoCmd() abort
    if exists('b:vime_table_name')
        call vime#info#show(b:vime_table_name)
    else
        echo 'VIME: No table loaded in this buffer'
    endif
endfunction

" ======================================================================
" Autocommands
" ======================================================================

augroup vime_filetype
    autocmd!
    autocmd BufReadCmd *.h5,*.hdf5 call vime#list#open(expand('<afile>:p'))
augroup END

augroup vime_lifecycle
    autocmd!
    autocmd VimLeave * if get(g:, 'vime_owns_server', 0) | call vime#http#stop_server() | endif
augroup END
