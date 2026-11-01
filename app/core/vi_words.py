"""Từ ghép tiếng Việt hay gặp trong thoại truyện: khi tự xuống dòng tránh cắt đôi (vd "Cảm | ơn").

Tiếng Việt viết cách từng âm tiết nên không biết đâu là ranh giới từ; danh sách này đủ cho các từ thông dụng,
từ lạ thì coi như ngắt được ở mọi chỗ (như trước).
"""
from __future__ import annotations

_WORDS = """
cảm ơn, xin lỗi, xin chào, tạm biệt, cám ơn, nhất định, trân trọng, kiểm soát, khoảnh khắc, nhỏ bé, to lớn, bây giờ,
từ giờ, lúc nãy, lúc này, lúc đó, khi nào, bao giờ, hôm nay, hôm qua, ngày mai, tối nay, sáng nay, chiều nay,
đêm nay, năm nay, tuần sau, lần sau, lần này, như vậy, như thế, thế nào, tại sao, vì sao, bởi vì, tại vì, cho nên,
vì vậy, nhưng mà, thế nhưng, tuy nhiên, nếu như, giá như, mặc dù, dù sao, dù vậy, thế thì, vậy thì, thì ra, hoá ra,
hóa ra, rốt cuộc, cuối cùng, đầu tiên, cái gì, chuyện gì, việc gì, điều gì, ai đó, cái đó, cái này, chỗ này, chỗ đó,
ở đây, ở đó, đằng kia, đằng ấy, thật sự, thực sự, có lẽ, chắc chắn, đương nhiên, tất nhiên, dĩ nhiên, hình như,
đôi khi, thỉnh thoảng, luôn luôn, mãi mãi, lúc nào, bao nhiêu, mấy giờ, tất cả, mọi người, mọi thứ, mọi chuyện,
một chút, một lát, một mình, một ngày, chút xíu, quá đáng, anh trai, chị gái, em gái, em trai, bố mẹ, cha mẹ, ba mẹ,
ông bà, gia đình, bạn bè, bạn gái, bạn trai, người yêu, cô gái, chàng trai, con gái, con trai, cô bé, cậu bé,
thiếu nữ, tiểu thư, công chúa, hoàng tử, đức vua, nữ hoàng, học sinh, sinh viên, giáo viên, thầy giáo, cô giáo,
lớp học, trường học, bài tập, kiểm tra, thi cử, câu lạc bộ, hội học sinh, tan học, nghỉ hè, lễ hội, sinh nhật,
quà tặng, món quà, bữa trưa, bữa tối, bữa sáng, cơm hộp, ăn cơm, ăn sáng, ăn tối, uống nước, đồ ăn, thức ăn, nấu ăn,
nhà bếp, phòng ngủ, căn phòng, ngôi nhà, cửa hàng, siêu thị, công viên, bãi biển, thành phố, đất nước, thế giới,
vũ trụ, bầu trời, mặt trời, mặt trăng, ngôi sao, mùa xuân, mùa hè, mùa thu, mùa đông, trời mưa, tuyết rơi, thời tiết,
thời gian, thời điểm, lần đầu, ngày xưa, tương lai, quá khứ, hiện tại, kỷ niệm, ký ức, giấc mơ, ước mơ, hy vọng,
hi vọng, mong muốn, mong chờ, chờ đợi, yêu thương, yêu quý, quý mến, thương yêu, thích thú, vui vẻ, hạnh phúc,
buồn bã, cô đơn, lo lắng, sợ hãi, hoảng sợ, bối rối, xấu hổ, ngại ngùng, ngượng ngùng, tức giận, giận dỗi, ghen tị,
ghen tuông, đau khổ, đau đớn, khó chịu, dễ chịu, dễ thương, đáng yêu, xinh đẹp, xinh xắn, đẹp trai, tuyệt vời,
tuyệt đối, hoàn hảo, kỳ lạ, kì lạ, bình thường, đặc biệt, quan trọng, nghiêm túc, thoải mái, an toàn, nguy hiểm,
cẩn thận, chăm chỉ, cố gắng, nỗ lực, chiến đấu, bảo vệ, giúp đỡ, giải thích, hiểu lầm, hiểu biết, nhận ra, phát hiện,
quyết định, lựa chọn, chuẩn bị, bắt đầu, kết thúc, tiếp tục, dừng lại, trở lại, quay lại, trở về, quay về, đi về,
về nhà, ra ngoài, vào trong, đi chơi, hẹn hò, gặp gỡ, gặp lại, chia tay, kết hôn, đám cưới, nắm tay, ôm chặt,
hôn nhau, cảm giác, cảm thấy, cảm xúc, tình cảm, tình yêu, trái tim, tâm trạng, suy nghĩ, ý nghĩ, ý kiến, ý tưởng,
câu chuyện, chuyện trò, nói chuyện, trò chuyện, tâm sự, lắng nghe, trả lời, câu hỏi, hỏi han, làm việc, công việc,
nhiệm vụ, sức mạnh, ma thuật, phép thuật, kẻ thù, đồng đội, đồng minh, chiến thắng, thất bại, thành công, sai lầm,
lỗi lầm, trách nhiệm, lời hứa, hứa hẹn, bí mật, sự thật, nói dối, lừa dối, tin tưởng, tin cậy, tôn trọng, ngưỡng mộ,
cơ thể, khuôn mặt, đôi mắt, mái tóc, bàn tay, đôi tay, giọng nói, nụ cười, mỉm cười, nước mắt, khóc lóc, ngủ ngon,
buồn ngủ, mệt mỏi, đói bụng, no bụng, sức khoẻ, sức khỏe, bị thương, bệnh viện, bác sĩ, chăm sóc, nghỉ ngơi,
thư giãn, điện thoại, tin nhắn, máy tính, trò chơi, âm nhạc, bài hát, ca hát, nhảy múa, bộ phim, quyển sách,
cuốn sách, truyện tranh, áo khoác, quần áo, đồng phục, váy áo, giày dép, tiền bạc, đồng tiền, mua sắm, giá cả,
đắt tiền, rẻ tiền, chính xác, nhanh lên, từ từ, chậm lại, im lặng, yên lặng, bình tĩnh, khoan đã, chờ chút, chờ đã,
đợi đã, đợi chút, trời ơi, chết tiệt, khốn kiếp, thật tình, thật ra, thực ra, nói thật, nói chung, ví dụ, chẳng hạn,
chính mình, bản thân, chúng ta, chúng tôi, chúng mình, bọn mình, bọn tôi, các cậu, các bạn, mọi người, người ta,
cậu ấy, cô ấy, anh ấy, chị ấy, em ấy, ông ấy, bà ấy, bạn ấy, hắn ta, cô ta, anh ta, phía trước, phía sau, bên cạnh,
bên trong, bên ngoài, xung quanh, đằng sau, trên đường, dọc đường, ngoài kia, trong lúc, trong khi, sau khi,
trước khi, cho đến, cho tới, mãi đến, đến nỗi, đến mức, hơn nữa, ngoài ra, thêm nữa, còn nữa, vẫn còn, vẫn chưa,
đã từng, sắp sửa, vừa mới, mới đây, gần đây, sớm muộn, mau chóng, nhanh chóng, lập tức, đột nhiên, bỗng nhiên,
tự nhiên, cố ý, vô tình, tình cờ, ngẫu nhiên, may mắn, xui xẻo, không may, đáng sợ, đáng ghét, đáng tiếc, thân thiết,
thân mật, gần gũi, xa cách, xa lạ, quen thuộc, làm quen, quen biết, bạn thân, người lạ, khách sạn, kỳ nghỉ,
chuyến đi, du lịch, tàu điện, xe buýt, ô tô, xe đạp, sân bay, nhà ga, con đường, góc phố, ngã tư, toàn bộ, tâm tư,
thật lòng, thành thật, chân thành, từ trước, ngồi xuống, đứng dậy, đứng lên, nằm xuống, thời kỳ, ơn nghĩa, cha con,
mẹ con, vợ chồng, cô dâu, chú rể, hôn nhân, thông gia, họ hàng, trưởng thành, lớn lên, chịu đựng, tha thứ, xin phép,
làm phiền, phiền phức, rắc rối, lo xa, yên tâm, an tâm, cảm động, xúc động, khó khăn, dễ dàng, đơn giản,
phức tạp, ít khi, có khi, lý do, lí do, tự do, nhân vật, hoạt hình, tụi mình, tụi tớ, như này, như kia, cao hơn,
ở trên, ở dưới, phía dưới, phía trên, công bằng
"""

COMPOUNDS: frozenset[tuple[str, str]] = frozenset(
    tuple(w.split()[i:i + 2])
    for phrase in _WORDS.replace("\n", " ").split(",") if (w := phrase.strip())
    for i in range(len(w.split()) - 1)
)
PUNCT = ".,!?…:;\"'()[]«»“”‘’-–—~*"
# Tiểu từ cuối câu: đứng một mình ở dòng cuối ("ạ.") trông như rơi chữ, nên dính vào từ trước.
PARTICLES = frozenset("ạ à ừ nhé nha nhỉ hả hở hử chứ đấy đây đó vậy thế mà đi nào thôi cơ kìa luôn chưa không".split())

# Hư từ đi với từ đứng sau (giới từ, lượng từ, trợ động từ, liên từ): đứng cuối dòng thì câu bị treo ("của | tôi").
LEADING = frozenset("""của với và cho là những các mỗi mọi một để từ ở tại trong trên dưới như bằng rằng nếu vì
bị sẽ đã đang rất hãy đừng chớ không chẳng chả mà cái""".split())


def bare(word: str) -> str:
    return word.strip(PUNCT).lower()


def joined(left: str, right: str) -> bool:
    """`left right` là một từ ghép, không nên xuống dòng ở giữa (dấu câu ở giữa thì ngắt được)."""
    if left.rstrip(PUNCT) != left or right.lstrip(PUNCT) != right:
        return False
    return (bare(left), bare(right)) in COMPOUNDS


def final_particle(left: str, right: str) -> bool:
    """`right` là tiểu từ kết câu ("rất nhiều ạ."): xuống dòng trước nó thì tiểu từ đứng trơ một mình."""
    return left.rstrip(PUNCT) == left and right.rstrip(PUNCT) != right and bare(right) in PARTICLES


def ends_clause(word: str) -> bool:
    """Từ kết thúc một vế câu (dấu phẩy, chấm…): chỗ xuống dòng tự nhiên."""
    return word.rstrip("\"'”’)»*")[-1:] in ",.!?…:;"


def dangling(left: str, right: str, before: str = "") -> bool:
    """`left` là hư từ đi với `right` ("của tôi", "những người"): xuống dòng giữa hai từ thì câu bị treo.
    `before` = từ đứng trước `left`: nếu `before left` là từ ghép ("ít khi", "tự do") thì `left` không phải hư từ."""
    return (left.rstrip(PUNCT) == left and right.lstrip(PUNCT) == right and bare(left) in LEADING
            and not (before and joined(before, left)))
