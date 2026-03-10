import feedparser
import re
import os
import datetime
import time
from rfeed import Item, Feed, Guid
from email.utils import parsedate_to_datetime

# --- 配置区域 ---
OUTPUT_FILE = "filtered_feed.xml"  # 输出文件
OUTPUT_FILE_24H = "24hours.xml"    # 最近24小时文章输出文件
MAX_ITEMS = 10000                    # RSS中保留的最大条目数（滚动窗口）
JOURNALS_FILE = 'journals.dat'
KEYWORDS_FILE = 'keywords.dat'
# ----------------

def load_config(filename, env_var_name=None):
    """
    优先从环境变量读取配置（用于 GitHub Actions 保护隐私），
    如果环境变量不存在，则读取本地文件（用于本地测试）。
    """
    # 1. 尝试读取环境变量 (Secrets)
    if env_var_name and os.environ.get(env_var_name):
        print(f"Loading config from environment variable: {env_var_name}")
        # 假设环境变量里用分号 ; 或者换行符分隔
        content = os.environ[env_var_name]
        # 兼容换行符或分号分隔
        if '\n' in content:
            return [line.strip() for line in content.split('\n') if line.strip()]
        else:
            return [line.strip() for line in content.split(';') if line.strip()] 
            
    # 2. 尝试读取本地文件
    if os.path.exists(filename):
        print(f"Loading config from local file: {filename}")
        with open(filename, 'r', encoding='utf-8') as f:
            return [line.strip() for line in f if line.strip() and not line.startswith('#')]
            
    print(f"Warning: No config found for {filename} or {env_var_name}")
    return []

def convert_struct_time_to_datetime(struct_time):
    """将 feedparser 的时间结构转换为 datetime 对象"""
    if not struct_time:
        return datetime.datetime.now()
    return datetime.datetime.fromtimestamp(time.mktime(struct_time))

def parse_rss(rss_url, retries=3):
    """解析在线 RSS 订阅"""
    print(f"Fetching: {rss_url}...")
    for attempt in range(retries):
        try:
            feed = feedparser.parse(rss_url)
            entries = []
            journal_title = feed.feed.get('title', 'Unknown Journal')
            
            for entry in feed.entries:
                # 获取标准时间
                pub_struct = entry.get('published_parsed', entry.get('updated_parsed'))
                pub_date = convert_struct_time_to_datetime(pub_struct)
                
                entries.append({
                    'title': entry.get('title', ''),
                    'link': entry.get('link', ''),
                    'pub_date': pub_date,
                    'summary': entry.get('summary', entry.get('description', ''),
                    'journal': journal_title,
                    'id': entry.get('id', entry.get('link', '')) # ID 用于去重
                })
            return entries
        except Exception as e:
            print(f"Error parsing {rss_url}: {e}")
            time.sleep(2)
    return []

def get_existing_items():
    """读取上一次生成的 XML 文件，保留历史数据"""
    if not os.path.exists(OUTPUT_FILE):
        return []
    
    print(f"Loading existing items from {OUTPUT_FILE}...")
    try:
        # feedparser 也可以解析本地 XML 文件
        feed = feedparser.parse(OUTPUT_FILE)
        entries = []
        for entry in feed.entries:
            # 恢复 datetime 对象
            pub_struct = entry.get('published_parsed')
            pub_date = convert_struct_time_to_datetime(pub_struct)
            
            # 注意：生成的 XML 标题通常是 "[Journal] Title"，这里我们需要尽量保持原样
            # 或者为了简单起见，我们直接存储读取到的内容
            entries.append({
                'title': entry.get('title', ''), # 这里标题已经包含 [Journal] 前缀了
                'link': entry.get('link', ''),
                'pub_date': pub_date,
                'summary': entry.get('summary', ''),
                'journal': entry.get('author', ''), # 我们在生成时把 journal 存入了 author 字段
                'id': entry.get('id', entry.get('link', '')), 
                'is_old': True # 标记为旧数据，不需要再次关键词匹配
            })
        return entries
    except Exception as e:
        print(f"Error reading existing file: {e}")
        return []

def match_entry(entry, queries):
    """关键词匹配"""
    # 构造待搜索文本
    text_to_search = (entry['title'] + " " + entry['summary']).lower()
    
    for query in queries:
        keywords = [k.strip().lower() for k in query.split('AND')]
        match = True
        for keyword in keywords:
            # 使用简单的字符串包含判断，比正则更快，且对科研关键词通常足够
            if keyword not in text_to_search:
                match = False
                break
        if match:
            return True
    return False

def generate_rss_xml(items):
    """生成 RSS 2.0 XML 文件"""
    rss_items = []
    
    # 按时间倒序排列（最新的在最前）
    # 确保所有 item 都有 pub_date 且是 datetime 对象
    items.sort(key=lambda x: x['pub_date'], reverse=True)
    
    # 截取最新的 MAX_ITEMS 条
    items = items[:MAX_ITEMS]
    
    for item in items:
        # 如果是旧数据，标题可能已经是 "[Journal] Title" 格式，需要避免重复添加前缀
        title = item['title']
        if not item.get('is_old', False):
            # 新数据，添加期刊前缀
            title = f"[{item['journal']}] {item['title']}"
            
        rss_item = Item(
            title = title,
            link = item['link'],
            description = item['summary'],
            author = item['journal'], # 借用 author 字段存储期刊名
            guid = Guid(item['id']),
            pubDate = item['pub_date']
        )
        rss_items.append(rss_item)

    feed = Feed(
        title = "My Customized Papers (Auto-Filtered)",
        link = "https://github.com/your_username/your_repo",
        description = "Aggregated research papers based on keywords",
        language = "en-US",
        lastBuildDate = datetime.datetime.now(),
        items = rss_items
    )

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(feed.rss())
    print(f"Successfully generated {OUTPUT_FILE} with {len(rss_items)} items.")

def generate_24h_rss_xml(items):
    """从所有条目中筛选出最近24小时内发布的文章，生成单独的 RSS XML 文件"""
    now = datetime.datetime.now()
    cutoff_time = now - datetime.timedelta(hours=24)
    
    # 筛选最近24小时内的文章
    recent_items = [item for item in items if item['pub_date'] >= cutoff_time]
    
    # 按时间倒序排列（最新的在最前）
    recent_items.sort(key=lambda x: x['pub_date'], reverse=True)
    
    rss_items = []
    for item in recent_items:
        # 如果是旧数据，标题可能已经是 "[Journal] Title" 格式，需要避免重复添加前缀
        title = item['title']
        if not item.get('is_old', False):
            # 新数据，添加期刊前缀
            title = f"[{item['journal']}] {item['title']}"
            
        rss_item = Item(
            title = title,
            link = item['link'],
            description = item['summary'],
            author = item['journal'],
            guid = Guid(item['id']),
            pubDate = item['pub_date']
        )
        rss_items.append(rss_item)

    feed = Feed(
        title = "My Customized Papers - Last 24 Hours",
        link = "https://github.com/your_username/your_repo",
        description = "Research papers from the last 24 hours, filtered by keywords",
        language = "en-US",
        lastBuildDate = datetime.datetime.now(),
        items = rss_items
    )

    with open(OUTPUT_FILE_24H, "w", encoding="utf-8") as f:
        f.write(feed.rss())
    print(f"Successfully generated {OUTPUT_FILE_24H} with {len(rss_items)} items (last 24 hours).")

def main():
    # 1. 读取配置
    rss_urls = load_config('journals.dat', 'RSS_JOURNALS') 
    queries = load_config('keywords.dat', 'RSS_KEYWORDS')
    
    if not rss_urls or not queries:
        print("Error: Configuration files are empty or missing.")
        return

    # 2. 读取旧数据（核心去重策略：保留历史）
    existing_entries = get_existing_items()
    # 创建一个已有 ID 的集合，用于快速查重
    seen_ids = set(entry['id'] for entry in existing_entries)
    
    all_entries = existing_entries.copy()
    new_count = 0

    # 3. 抓取新数据
    print("Starting RSS fetch from remote...")
    for url in rss_urls:
        fetched_entries = parse_rss(url)
        for entry in fetched_entries:
            # 查重：如果 ID 已经在旧数据里，直接跳过
            if entry['id'] in seen_ids:
                continue
            
            # 关键词匹配
            if match_entry(entry, queries):
                all_entries.append(entry)
                seen_ids.add(entry['id'])
                new_count += 1
                print(f"Match found: {entry['title'][:50]}...")

    print(f"Added {new_count} new entries. Total entries before limit: {len(all_entries)}")
    
    # 4. 生成完整的 RSS 文件 (包含排序和截断)
    generate_rss_xml(all_entries)
    
    # 5. 筛选最近24小时的文章，生成 24hours.xml
    generate_24h_rss_xml(all_entries)

if __name__ == '__main__':
    main()