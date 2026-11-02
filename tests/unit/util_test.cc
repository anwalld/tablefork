#include <drogon/drogon_test.h>

#include "common/util.h"

using chat::isPrintableText;
using chat::utf8Length;

DROGON_TEST(Utf8LengthCountsCodePoints)
{
    CHECK(utf8Length("") == 0U);
    CHECK(utf8Length("abc") == 3U);
    CHECK(utf8Length("\xE4\xBD\xA0\xE5\xA5\xBD") == 2U);
    CHECK(utf8Length("\xF0\x9F\x98\x80") == 1U);
}

DROGON_TEST(Utf8LengthRejectsInvalidText)
{
    CHECK(!utf8Length("\xFF").has_value());
    CHECK(!utf8Length("\xE4\xBD").has_value());
    CHECK(!utf8Length("\xC0\x80").has_value());
    CHECK(!utf8Length("\xED\xA0\x80").has_value());
}

DROGON_TEST(PrintableTextRejectsControlCharacters)
{
    using namespace std::string_view_literals;
    CHECK(isPrintableText("abc"));
    CHECK(isPrintableText("\xE4\xBD\xA0 \xF0\x9F\x98\x80"));
    CHECK(!isPrintableText("a\0b"sv));
    CHECK(!isPrintableText("a\nb"));
    CHECK(!isPrintableText("a\x7F"));
    CHECK(!isPrintableText("a\xC2\x85"));
    CHECK(!isPrintableText("\xFF"));
}
