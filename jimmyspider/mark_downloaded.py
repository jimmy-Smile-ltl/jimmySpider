import csv
import json
import logging
import hashlib
from typing import Dict, List, Set, Optional
from pymongo import MongoClient, UpdateOne
from collections import defaultdict
from jimmyspider.config import get_config
from jimmyspider.log_print import LogPrint
from pathlib import Path

class DownloadMarker:
    """下载状态标记器"""
    
    def __init__(self, 
                 check_mongo_uri=None,
                 check_db_name="all_journals",
                 check_collection="all_articles_by_doi"):
        """
        初始化标记器
        
        Args:
            check_mongo_uri: 查询下载状态的MongoDB连接URI
            check_db_name: 查询下载状态的数据库名称
            check_collection: 查询下载状态的集合名称（all_articles_by_doi）
        """
        if check_mongo_uri is None:
            check_mongo_uri = get_config().MONGO_URI
        self.check_mongo_uri = check_mongo_uri
        self.check_db_name = check_db_name
        self.check_collection_name = check_collection
        
        # 连接查询下载状态的MongoDB
        self.check_client = MongoClient(check_mongo_uri, serverSelectionTimeoutMS=5000)
        self.check_client.admin.command('ping')
        self.check_db = self.check_client[check_db_name]
        self.check_collection = self.check_db[check_collection]
        self.project_root = Path(__file__).parent
        log_dir = self.project_root.joinpath("./logs")
        spider_name = "mark_downloaded_by_doi"
        self.log_print = LogPrint(log_dir =log_dir,  name =spider_name )
        self.log_print.info(f"✅ 连接查询数据库成功: {check_db_name}.{check_collection}")
        
        # 用于源数据库的连接（MongoDB模式时使用）
        self.source_client = None
    
    def normalize_doi(self, doi: str) -> str:
        """标准化DOI：转小写、去空格、移除URL前缀"""
        if not doi:
            return ''
        doi = doi.replace('https://doi.org/', '').replace('http://doi.org/', '')
        doi = doi.replace('https://dx.doi.org/', '').replace('http://dx.doi.org/', '')
        return str(doi).lower().strip()
    
    def check_downloaded_dois(self, dois: List[str], batch_size: int = 1000) -> Set[str]:
        """
        批量查询DOI是否已下载（查询all_articles_by_doi集合的move_success字段）
        
        Args:
            dois: DOI列表
            batch_size: 批次大小
        
        Returns:
            已下载的DOI集合（标准化后的）
        """
        if not dois:
            return set()
        
        # 标准化所有DOI
        normalized_dois = list(set([self.normalize_doi(doi) for doi in dois if self.normalize_doi(doi)]))
        
        self.log_print.info(f"📊 标准化后得到 {len(normalized_dois):,} 个唯一DOI")
        
        # all_articles_by_doi 集合的 _id 是 DOI 的 MD5 哈希
        # 构建 DOI 到 _id 的映射（使用 MD5 哈希）
        doi_to_id_map = {}
        id_to_doi_map = {}
        for doi in normalized_dois:
            # 计算 MD5 哈希（使用小写的 DOI）
            md5_hash = hashlib.md5(doi.encode('utf-8')).hexdigest()
            doi_to_id_map[doi] = md5_hash
            id_to_doi_map[md5_hash] = doi
        
        # 批量查询比对目标集合
        # all_possible_ids 是 MD5 哈希列表（_id列表）
        all_possible_ids = list(id_to_doi_map.keys())
        total_batches = (len(all_possible_ids) + batch_size - 1) // batch_size
        
        self.log_print.info(f"🔍 开始查询下载状态，分 {total_batches} 批处理...")
        self.log_print.info(f"   将查询 {len(all_possible_ids):,} 个 _id（MD5哈希）")
        
        downloaded_dois = set()
        
        for i in range(0, len(all_possible_ids), batch_size):
            batch_ids = all_possible_ids[i:i + batch_size]  # batch_ids 是 MD5 哈希列表
            batch_num = i // batch_size + 1
            
            self.log_print.info(f"  处理批次 {batch_num}/{total_batches} ({len(batch_ids):,} 个 _id)...")
            
            try:
                # 查询 all_articles_by_doi 集合
                # _id 是 MD5 哈希，查询 move_success=True 的记录
                move_success_dois = set()
                
                cursor = self.check_collection.find(
                    {'_id': {'$in': batch_ids}}, 
                    {'_id': 1, 'move_success': 1}
                ).max_time_ms(30000)  # 30秒超时
                
                for doc in cursor:
                    doc_id = doc['_id']
                    move_success = doc.get('move_success', False)
                    if move_success and doc_id in id_to_doi_map:
                        move_success_dois.add(id_to_doi_map[doc_id])
                
                downloaded_dois.update(move_success_dois)
                self.log_print.info(f"    找到 {len(move_success_dois)} 个 move_success=True 的记录")
                
            except Exception as e:
                self.log_print.warning(f"    批次 {batch_num} 查询失败: {e}，跳过")
                import traceback
                self.log_print.error(traceback.format_exc())
                continue
        
        self.log_print.info(f"✅ 查询完成，共找到 {len(downloaded_dois):,} 个已下载的DOI")
        return downloaded_dois
    
    def mark_csv_file(self, 
                     input_file: str,
                     output_file: str,
                     doi_column: str = 'doi',
                     status_column: str = 'downloaded',
                     batch_size: int = 1000):
        """
        标记CSV文件中的下载状态
        
        Args:
            input_file: 输入CSV文件路径
            output_file: 输出CSV文件路径
            doi_column: DOI列名
            status_column: 状态列名（如果不存在会创建）
            batch_size: 批次大小
        """
        self.log_print.info(f"📋 开始处理CSV文件: {input_file}")
        
        # 读取所有DOI
        dois = []
        rows = []
        doi_to_rows = defaultdict(list)  # DOI -> 行索引列表
        
        with open(input_file, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames
            
            if doi_column not in fieldnames:
                self.log_print.error(f"❌ CSV文件中没有找到DOI列: {doi_column}")
                return
            
            # 如果状态列不存在，添加到字段名列表
            if status_column not in fieldnames:
                fieldnames = list(fieldnames) + [status_column]
            
            for idx, row in enumerate(reader):
                rows.append(row)
                doi = row.get(doi_column, '').strip()
                if doi:
                    normalized = self.normalize_doi(doi)
                    if normalized:
                        dois.append(normalized)
                        doi_to_rows[normalized].append(idx)
        
        self.log_print.info(f"📊 读取了 {len(rows):,} 行，提取了 {len(dois):,} 个DOI")
        
        # 查询下载状态
        downloaded_dois = self.check_downloaded_dois(list(set(dois)), batch_size)
        
        # 更新行数据
        updated_count = 0
        for normalized_doi in downloaded_dois:
            for row_idx in doi_to_rows[normalized_doi]:
                rows[row_idx][status_column] = 'True'  # 或 '1'、'Yes' 等
                updated_count += 1
        
        # 写入输出文件
        self.log_print.info(f"💾 写入输出文件: {output_file}")
        with open(output_file, 'w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        
        self.log_print.info(f"✅ 标记完成: 更新了 {updated_count:,} 条记录")
        self.log_print.info(f"📊 统计: 总记录数 {len(rows):,}, 已下载 {len(downloaded_dois):,}, 更新 {updated_count:,}")
    
    def mark_mongodb_collection(self,
                                source_mongo_uri: str,
                                source_db_name: str,
                                source_collection_name: str,
                                doi_field: str = 'doi',
                                status_field: str = 'pdf_downloaded',
                                query_filter: Optional[Dict] = None,
                                batch_size: int = 1000):
        """
        标记MongoDB集合中的下载状态
        
        Args:
            source_mongo_uri: 源数据库MongoDB连接URI
            source_db_name: 源数据库名称
            source_collection_name: 源集合名称
            doi_field: DOI字段名
            status_field: 状态字段名
            query_filter: 查询过滤器（可选）
            batch_size: 批次大小
        """
        self.log_print.info(f"📋 开始处理MongoDB集合: {source_db_name}.{source_collection_name}")
        
        # 连接源数据库
        self.source_client = MongoClient(source_mongo_uri, serverSelectionTimeoutMS=5000)
        self.source_client.admin.command('ping')
        source_db = self.source_client[source_db_name]
        source_collection = source_db[source_collection_name]
        
        # 构建查询条件
        query = query_filter if query_filter else {}   # 没有这个字段 { "year": { "$gte": "2000", "$lt": "2015" } }
        
        # 获取所有DOI
        self.log_print.info("🔍 提取DOI列表...")
        dois = []
        doi_to_doc_ids = defaultdict(list)
        
        for doc in source_collection.find(query, {'_id': 1, doi_field: 1}):
            doi_raw = doc.get(doi_field) or ''
            doi = doi_raw.strip() if isinstance(doi_raw, str) else ''
            doc_id = doc.get('_id')
            if doi and doc_id:
                normalized = self.normalize_doi(doi)
                if normalized:
                    dois.append(normalized)
                    doi_to_doc_ids[normalized].append(doc_id)
        
        self.log_print.info(f"📊 找到 {len(dois):,} 个DOI，对应 {sum(len(ids) for ids in doi_to_doc_ids.values()):,} 个文档")
        
        # 查询下载状态
        downloaded_dois = self.check_downloaded_dois(list(set(dois)), batch_size)
        
        # 批量更新
        self.log_print.info("💾 开始批量更新...")
        updated_count = 0
        total_batches = (len(downloaded_dois) + batch_size - 1) // batch_size
        
        for i, normalized_doi in enumerate(downloaded_dois):
            if normalized_doi in doi_to_doc_ids:
                doc_ids = doi_to_doc_ids[normalized_doi]
                
                bulk_ops = []
                for doc_id in doc_ids:
                    bulk_ops.append(
                        UpdateOne(
                            {'_id': doc_id},
                            {'$set': {status_field: True}}
                        )
                    )
                
                if bulk_ops:
                    result = source_collection.bulk_write(bulk_ops, ordered=False)
                    updated_count += result.modified_count
                
                if (i + 1) % 100 == 0:
                    self.log_print.info(f"  进度: {i + 1}/{len(downloaded_dois)} ({updated_count:,} 条已更新)")
        
        self.log_print.info(f"✅ 标记完成: 更新了 {updated_count:,} 条记录")
        self.log_print.info(f"📊 统计: 总DOI数 {len(set(dois)):,}, 已下载 {len(downloaded_dois):,}, 更新 {updated_count:,}")
    
    def close(self):
        """关闭数据库连接"""
        if self.check_client:
            self.check_client.close()
            self.log_print.info("查询数据库连接已关闭")
        if self.source_client:
            self.source_client.close()
            self.log_print.info("源数据库连接已关闭")

# 
# def main():
#     """主函数"""
#     import argparse
#     # python mark_downloaded_by_doi.py  --mode mongodb --source-db jimmy --input jstage --doi-column doi
#     #  --mode mongodb --source-db jimmy --input jstage --doi-column doi
#     parser = argparse.ArgumentParser(description='根据DOI查询下载状态并标记')
#     parser.add_argument('--mode', choices=['csv', 'mongodb'], required=True,
#                        help='处理模式: csv 或 mongodb')
#     parser.add_argument('--input', type=str, help='输入文件（CSV模式）或集合名（MongoDB模式）')
#     parser.add_argument('--output', type=str, help='输出文件（仅CSV模式）')
#     parser.add_argument('--source-db', type=str, help='源数据库名（MongoDB模式）')
#     parser.add_argument('--doi-column', type=str, default='doi', help='DOI列名或字段名')
#     parser.add_argument('--status-column', type=str, default='downloaded', help='状态列名或字段名')
#     parser.add_argument('--check-mongo-uri', type=str, default=get_config().MONGO_URI, 
#                        help='查询下载状态的MongoDB连接URI')
#     parser.add_argument('--check-db', type=str, default='all_journals', help='查询下载状态的数据库名')
#     parser.add_argument('--check-collection', type=str, default='all_articles_by_doi', 
#                        help='查询下载状态的集合名')
#     parser.add_argument('--batch-size', type=int, default=1000, help='批次大小')
#     parser.add_argument('--source-mongo-uri', type=str, default='mongodb://localhost:27017/', 
#                        help='源数据库MongoDB连接URI（MongoDB模式）')
#     
#     args = parser.parse_args()
#     
#     # 创建标记器
#     marker = DownloadMarker(
#         check_mongo_uri=args.check_mongo_uri,
#         check_db_name=args.check_db,
#         check_collection=args.check_collection
#     )
#     
#     try:
#         if args.mode == 'csv':
#             if not args.input or not args.output:
#                 self.log_print.error("CSV模式需要指定 --input 和 --output 参数")
#                 return
#             
#             marker.mark_csv_file(
#                 input_file=args.input,
#                 output_file=args.output,
#                 doi_column=args.doi_column,
#                 status_column=args.status_column,
#                 batch_size=args.batch_size
#             )
#         
#         elif args.mode == 'mongodb':
#             if not args.input or not args.source_db:
#                 self.log_print.error("MongoDB模式需要指定 --input（集合名）和 --source-db 参数")
#                 return
#             
#             marker.mark_mongodb_collection(
#                 source_mongo_uri=args.source_mongo_uri,
#                 source_db_name=args.source_db,
#                 source_collection_name=args.input,
#                 doi_field=args.doi_column,
#                 status_field=args.status_column,
#                 batch_size=args.batch_size
#             )
#     
#     except Exception as e:
#         self.log_print.error(f"❌ 处理失败: {e}")
#         import traceback
#         self.log_print.error(traceback.format_exc())
#     finally:
#         marker.close()
# 
# 
# if __name__ == "__main__":
#     main()

