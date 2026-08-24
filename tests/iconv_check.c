/* 探测 mingw64 的 iconv 是否由 libc 直接提供（无 -liconv 也能链接）。 */
#include <iconv.h>
#include <stdio.h>
int main(void) {
    iconv_t c = iconv_open("UTF-8", "UTF-16LE");
    if (c == (iconv_t)-1) { printf("no iconv in libc\n"); return 1; }
    iconv_close(c);
    printf("iconv in libc OK\n");
    return 0;
}
