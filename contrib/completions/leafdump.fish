# Copyright 2026 Jeremy Melanson
# SPDX-License-Identifier: Apache-2.0

# fish completion for leafdump
#
# Format lists come from `leafdump -L --porcelain`, filtered to formats whose
# optional Python dependencies are installed, so uninstallable choices are
# never offered.

function __leafdump_formats --argument-names direction
    leafdump -L --porcelain --direction $direction 2>/dev/null | while read -l line
        set -l f (string split \t -- $line)
        test (count $f) -ge 6; or continue
        test "$f[4]" = yes; or continue
        printf '%s\t%s\n' $f[1] $f[6]
        for a in (string split , -- $f[5])
            test -n "$a"; and printf '%s\t%s\n' $a "alias for $f[1]"
        end
    end
end

function __leafdump_templates
    leafdump --list-templates --porcelain 2>/dev/null | while read -l line
        set -l f (string split \t -- $line)
        test (count $f) -ge 5; or continue
        printf '%s\t%s\n' $f[1] $f[5]
        for a in (string split , -- $f[3])
            test -n "$a"; and printf '%s\t%s\n' $a "alias for $f[1]"
        end
    end
end

complete -c leafdump -f -a '(__fish_complete_path)'

# input
complete -c leafdump -s i -s f -l input-format -l from -r -f \
    -a "auto\t'detect from extension, then content' (__leafdump_formats in)" \
    -d 'Format of the input'
complete -c leafdump -s m -l multipacket -d 'Treat each input line as a separate document'
complete -c leafdump -s r -l reverse      -d 'Process documents newest-first (implies -m)'
complete -c leafdump -l input-encoding -r -f -a 'utf-8 utf-8-sig utf-16 latin-1 ascii cp1252' \
    -d 'Character encoding for text input'

# output
complete -c leafdump -s o -s t -l output-format -l to -r -f \
    -a '(__leafdump_formats out)' -d 'Format to emit'
complete -c leafdump -s O -l output -r -d 'Write to FILE instead of stdout'
complete -c leafdump -l indent  -r -f -d 'Indentation width for structured output'
complete -c leafdump -l compact       -d 'Single-line structured output'
complete -c leafdump -s s -l sort-keys -d 'Sort mapping keys'
complete -c leafdump -l ascii         -d 'Escape non-ASCII characters'
complete -c leafdump -l null-policy -r -f -a 'keep drop empty' \
    -d 'Handling of null in formats that lack it'
complete -c leafdump -l avro-schema -r -d 'Avro writer schema (JSON) instead of inference'

# path dump
complete -c leafdump -s T -l template -r -a '(__leafdump_templates)' \
    -d 'Pseudocode notation, by name or template file'
complete -c leafdump -s e -l escape-special -d 'Escape CR, LF and TAB in leaf values'
complete -c leafdump -l perl-compat -d 'Reproduce json_dump.pl byte-for-byte'
complete -c leafdump -l root -r -f -d 'Name of the root node in dumped paths'

# merging
complete -c leafdump -l no-merge -d 'Emit each document separately'
complete -c leafdump -l merge-strategy -r -f -d 'How to combine mappings' -a "\
deep\t'recurse, later wins at the leaves'
shallow\t'later document replaces the whole value'
last\t'later document wins outright'
first\t'earlier document wins outright'
collect\t'gather conflicting values into a list'"
complete -c leafdump -l list-merge -r -f -d 'How to combine arrays' -a "\
concat\t'append'
union\t'append, dropping structural duplicates'
replace\t'later wins'
keep\t'earlier wins'
index\t'merge element-wise by position'"
complete -c leafdump -s d -l dedup -d 'Drop structurally duplicate array elements'
complete -c leafdump -l wrap-key -r -f -d 'Key each document by its source' -a "\
none\t'merge instead'
basename\t'file name'
stem\t'file name without extension'
path\t'path as given'
index\t'position on the command line'"

# information
complete -c leafdump -s h -l help    -d 'Show help and exit'
complete -c leafdump -s L -l list-formats -d 'List every format and its availability'
complete -c leafdump -l help-format -r -f -a '(__leafdump_formats any)' \
    -d 'Explain one format in detail'
complete -c leafdump -l list-templates -d 'List every pseudocode template'
complete -c leafdump -l help-template -r -f -a '(__leafdump_templates)' \
    -d 'Explain one template, or how to write one'
complete -c leafdump -l dump-template -r -f -a '(__leafdump_templates)' \
    -d 'Print a template as JSON, ready to edit'
complete -c leafdump -l porcelain -d 'Machine-readable --list-formats output'
complete -c leafdump -l direction -r -f -a 'any in out' -d 'Restrict --list-formats'
complete -c leafdump -s V -l version -d 'Show version and exit'
