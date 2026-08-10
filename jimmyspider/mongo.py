import sys
import threading
import queue
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Any, Callable, Optional, List

import pymongo
from pymongo import MongoClient
from pymongo import UpdateOne

from jimmyspider.config import get_config


class HandleMongoDB:
    def __init__(self,table_name , record_repeat =False):
        config = get_config()
        self.client = MongoClient(config.MONGO_URI, serverSelectionTimeoutMS=5000)
        self.db = self.client[config.MONGO_DB]
        self.collection = self.db[table_name]
        self.record_repeat = record_repeat
        self.record_repeat =record_repeat
        if self.record_repeat:
            self.collection_repeated = self.db[f"{table_name}_repeated"]

    def insert_one(self,doc):
        try:
            result = self.collection.update_one(filter={"_id": doc["_id"]},update={"$set":doc},upsert=True)
            if result and result.upserted_id:
                print(f"新插入文档，_id = {result.upserted_id}")
            elif result and result.matched_count:
                print(f"更新了已存在的文档，modified = {result.modified_count}")
            return result
        except Exception as e:
            print(f"error in insert_one {str(e)}")
            return None

    def deduplicate_by_last_id(self,lst):
        """
        按照 '_id' 去重，保留最后出现的字典
        """
        # 记录每个 _id 最后出现的索引
        last_index = {}
        for i, d in enumerate(lst):
            last_index[d['_id']] = i

        # 按原顺序输出，只保留索引等于最后出现索引的元素
        result = []
        for i, d in enumerate(lst):
            if i == last_index[d['_id']]:
                result.append(d)
        print(f"清洗前 ： {len(lst)} , 清洗后 {len(result)} 是否减少 {len(lst) != len(result)} ")
        return result

    def insert_many(self, docs):
        docs = self.deduplicate_by_last_id(docs)
        """批量 upsert：根据 _id 更新或插入"""
        try:
            ops = [UpdateOne({"_id": d["_id"]}, {"$set": d}, upsert=True) for d in docs]
            result = self.collection.bulk_write(ops)
            print(f"insert_many 成功匹配了 {result.matched_count} 个文档 , 修改了 {result.modified_count} 个文档, 插入了 {result.upserted_count} 个新文档 ")
            if result.modified_count:
                # 构建每个文档的结果列表
                repeat_docs = []
                for idx, doc in enumerate(docs):
                    if idx in result.upserted_ids:
                        pass
                        # details.append({"_id": doc["_id"], "action": "insert"})
                    else:
                        # details.append({"_id": doc["_id"], "action": "update"})
                        # 避免再次重复
                        if self.record_repeat:
                            doc["id"] = doc["_id"] # 不自己给id了，避免报错 也避免覆盖
                            doc.pop("_id")
                            repeat_docs.append(doc)
                if self.record_repeat and len(repeat_docs) > 0:
                    self.collection_repeated.insert_many(repeat_docs)
            return result
        except Exception as e:
            print(f"批量 upsert 失败:,现在进行一行一行插入 {e}")
            # 降级方案：逐条处理
            for doc in docs:
                self.insert_one(doc)
            return None

    def get_collection(self):
        return self.collection
        # ==================== 新增的批量更新方法 ====================


    def _process_batch(self, docs: List[Dict], update_func: Callable,how ="one_by_one") -> tuple:
        """
        对一个批次的文档执行更新逻辑，生成批量写操作列表
        返回 (ops_list, updated_count)
        """
        ops = []
        updated_count = 0
        if how == "one_by_one":
            for doc in docs:
                update_doc = update_func(doc)
                if update_doc is not None:
                    # 只更新指定的字段，避免覆盖其他字段
                    ops.append(UpdateOne({"_id": doc["_id"]}, {"$set": update_doc}))
                    updated_count += 1
            return ops, updated_count
        else:
            doc_list = update_func(docs)
            updated_count += len(doc_list)
            for doc in doc_list:
                ops.append(UpdateOne({"_id": doc["_id"]}, {"$set": doc}))
            return ops, updated_count



    def update_batch_in_bulk(
            self,
            filter: Dict[str, Any],
            update_func: Callable[[Dict[str, Any]], Optional[Dict[str, Any]]],
            batch_size: int = 1000,
            sort_field: str = "_id",
            sort_way:int =  pymongo.ASCENDING,
            resume_from_id: Optional[str] = None,  # 新增：从哪个 _id 之后继续
            checkpoint_callback: Optional[Callable[[str], None]] = None,
            how = "one_by_one",
            logger = None
    ) -> int:
        """
        分批读取并批量更新文档（支持断点续传）

        :param filter: 查询条件
        :param update_func: 更新函数
        :param batch_size: 每批处理数量
        :param sort_field: 排序字段（推荐 _id）
        :param sort_way  排序方式
        :param resume_from_id: 从该 _id 之后继续处理（不包含该 id 本身）
        :param checkpoint_callback: 每批完成后回调，参数为最后处理的 _id，用于保存检查点
        :return: 实际更新的文档数量
        """
        total_updated = 0
        batch_num = 0
        last_id = None
        start_time = time.time()
        if not logger:
            raise  ValueError("logger 必须给")
        # 构建查询条件，实现断点续传
        query = filter.copy() if filter else {}
        if resume_from_id:
            # 注意：需要根据实际 _id 类型（ObjectId 或字符串）转换
            from bson.objectid import ObjectId
            try:
                # 假设 _id 是 ObjectId 类型
                resume_id = ObjectId(resume_from_id)
            except:
                # 如果是字符串类型
                resume_id = resume_from_id
            if sort_way == pymongo.ASCENDING:
                query[sort_field] = {"$gt": resume_id}
            else:
                query[sort_field] = {"$lt": resume_id}
        total_count = self.collection.count_documents(query)
        logger.print(f"符合条件的文档总数: {total_count}")
        cursor = self.collection.find(
            filter =query,
            no_cursor_timeout = True
                                      ).sort(sort_field, sort_way).batch_size(batch_size)
        try:
            docs_buffer = []
            for doc in cursor:
                docs_buffer.append(doc)
                if len(docs_buffer) >= batch_size:
                    start_time_batch = time.time()
                    ops, updated_count = self._process_batch(docs_buffer, update_func,how=how)
                    if ops:
                        self.collection.bulk_write(ops)
                        total_updated += updated_count
                    last_id = docs_buffer[-1]["_id"]
                    if checkpoint_callback:
                        checkpoint_callback(str(last_id))  # 持久化保存
                    docs_buffer = []
                    batch_num += 1
                    end_time = time.time()
                    logger.print(f"第 {batch_num} 批更新完成，共更新 {total_updated}/{total_count} 条文档，耗时：{end_time -start_time :.4f} "
                          f",平均一条 { (end_time -start_time +1 ) / (total_updated +1) :.4f} "
                          f" 当前批次 {updated_count} 条  耗时 {end_time -start_time_batch :.4f} 平均一条 { (end_time -start_time_batch +1 ) / (updated_count +1) :.4f}  "
                          f" 最后处理的 _id = {last_id} ")

            if docs_buffer:
                ops, updated_count = self._process_batch(docs_buffer, update_func,how=how)
                if ops:
                    self.collection.bulk_write(ops)
                    total_updated += updated_count
                last_id = docs_buffer[-1]["_id"]
                if checkpoint_callback:
                    checkpoint_callback(str(last_id))
            end_time = time.time()
            logger.print(f"批量更新完成，共更新 {total_updated} /{total_count} 条文档， 耗时：{end_time -start_time :.4f} ,平均一条 { (end_time -start_time +1 )/(total_updated +1) :.4f}  最后处理的 _id = {last_id}")
            return total_updated
        except Exception as e:
            logger.error(f"error in update_batch_in_bulk {str(e)}")
            cursor.close()
        finally:
            cursor.close()
    def update_batch_in_bulk_loop(
            self,
            filter: Dict[str, Any],
            update_func: Callable[[Dict[str, Any]], Optional[Dict[str, Any]]],
            sort_field: str = "_id",
            sort_way: int = pymongo.ASCENDING,
            resume_from_id: Optional[str] = None,
            checkpoint_callback: Optional[Callable[[str], None]] = None,
            batch_size: int = 100,
            max_workers: int = 20,
            logger=None,
            page_size: int = 1000,  # 分页查询每页大小，避免长 cursor 超时
            file_field = "pdf_url",
            flush_state : Callable[[Dict[str, Any]], Optional[Dict[str, Any]]] = None,
    ) -> int:
        """

        一定要注意 filter 的值 update 后 就需要 不满足条件 不然会不断循环
        多线程并发更新：分页查询替代长 cursor，Semaphore 控流，Condition 精确触发写入。
        - 分页查询：每批重新建 cursor，彻底避免 CursorNotFound
        - 工作线程池：只执行 update_func，结果放入 results_buffer
        - Semaphore：任务完成立即释放名额，主线程零轮询
        - Condition：精确触发写入，不丢通知，不漏数据
        """
        total_updated = 0
        total_successful = 0
        total_successful_temp = -1 # 一个 batch 全部失败 刷新
        total_failed_temp = -1
        total_failed = 0
        batch_num = 0
        start_time = time.time()
        results_buffer = []
        writing_done = threading.Event()
        # Condition 内置锁，同时作为 results_lock 使用
        write_condition = threading.Condition()
        semaphore = threading.Semaphore(max_workers)
        # total_count = self.collection.count_documents(filter)
        # logger.print(f"符合条件的文档总数: {total_count}")
        # 构建查询，支持断点续传
        query = filter.copy() if filter else {}
        if resume_from_id:
            from bson.objectid import ObjectId
            try:
                resume_id = ObjectId(resume_from_id)
            except Exception:
                resume_id = resume_from_id
            if sort_way == pymongo.ASCENDING:
                query[sort_field] = {"$gt": resume_id}
            else:
                query[sort_field] = {"$lt": resume_id}
        total_count = self.collection.count_documents(query)
        logger.print(f"符合条件的文档总数: {total_count}")
        # ── 分页迭代器，替代长 cursor ──────────────────────────────────
        def iter_docs():
            while True:
                page_query = query.copy()
                batch = list(
                    self.collection.find(filter=page_query)
                    .sort(sort_field, sort_way)
                    .limit(page_size)
                )
                if not batch:
                    print("没有更多文档了，分页迭代器结束")
                    break
                for doc in batch:
                    yield doc
                if len(batch) < page_size:
                    print("最后一页了，分页迭代器结束")
                    break

        # ── 写入线程 ───────────────────────────────────────────────────
        def writer_thread():
            nonlocal total_updated, batch_num
            while True:
                with write_condition:
                    # 等待：buffer 够了 或 全部任务完成
                    write_condition.wait_for(
                        lambda: len(results_buffer) >= batch_size or writing_done.is_set()
                    )
                    if not results_buffer:
                        if writing_done.is_set():
                            break
                        continue
                    # 持锁期间取走 buffer，工作线程此时只能等锁，不会并发写 buffer
                    batch = results_buffer[:]
                    results_buffer.clear()

                # 锁外执行 bulk_write，不阻塞工作线程往 buffer 写
                writer_start = time.time()
                ops = [UpdateOne({"_id": doc["_id"]}, {"$set": doc}) for doc in batch]
                if ops:
                    self.collection.bulk_write(ops)
                    total_updated += len(ops)
                    batch_num += 1
                    last_id = batch[-1]["_id"]
                    if checkpoint_callback:
                        checkpoint_callback(str(last_id))
                    if logger:
                        elapsed = time.time() - start_time
                        writer_end = time.time()
                        logger.print(
                            f"batch {batch_num} 写入 {len(ops)}， 当前进度 {total_successful}/{total_count} 条， 写入耗时 {writer_end - writer_start :.4f} ; 累计 {total_updated} 条，"
                            f"耗时 {elapsed:.2f}s，均速 {elapsed / max(total_updated, 1):.4f}s/条，"
                            f"last_id={last_id}"
                        )
                    # if flush_state:
                    #     flush_state()

        # ── Future 回调 ────────────────────────────────────────────────
        def on_future_done(future, doc):
            nonlocal total_failed, total_successful , total_successful_temp, total_failed_temp
            doc_id = doc["_id"]
            try:
                result = future.result()
            except Exception as e:
                if logger:
                    logger.error(f"任务 {doc_id} 失败: {e}")
                result = None
                total_failed += 1

            sys.stdout.flush()
            if result and isinstance(result, dict):
                total_successful += 1
                with write_condition:
                    results_buffer.append(result)
                    # buffer 够了就精确唤醒写入线程，持锁内操作不会漏通知
                    if len(results_buffer) >= batch_size:
                        write_condition.notify()
            else:
                total_failed += 1
                print(f"\r任务 {doc_id} 没有返回有效结果 ,当前返回结果 {result}", end="\t")
            print(f"\r成功 {total_successful} 失败 {total_failed}", end="\t")
            sys.stdout.flush()
            if total_failed > 1 and total_failed % batch_size == 0  and total_failed_temp != total_failed:
                file_url= doc.get(file_field, "未知URL")
                print(f"\n失败已达 {total_failed} 条，当前依然失败 抽查失败 url doc_id：{doc_id} {file_url}" )
                total_failed_temp = total_failed
                #刷新 相当于是连续失败 一个 batch_size
                if flush_state and total_successful_temp == total_successful:
                    flush_state()
                total_successful_temp = total_successful

            semaphore.release()

        writer = threading.Thread(target=writer_thread, name="mongo_writer", daemon=True)
        writer.start()

        try:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                for doc in iter_docs():
                    semaphore.acquire()
                    future = executor.submit(update_func, doc)
                    future.add_done_callback(lambda f, d=doc : on_future_done(f, doc=d))

                # 等所有在飞任务落地
                for _ in range(max_workers):
                    semaphore.acquire()

        except Exception as e:
            if logger:
                logger.error(f"主循环异常: {e}")
        finally:
            for _ in range(max_workers):
                semaphore.release()

        # 通知写入线程处理剩余 buffer
        with write_condition:
            writing_done.set()
            write_condition.notify()
        writer.join()

        elapsed = time.time() - start_time
        if logger:
            logger.print(
                f"完成，共更新 {total_updated} 条，成功 {total_successful}，失败 {total_failed}，"
                f"总耗时 {elapsed:.2f}s，均速 {elapsed / max(total_updated, 1):.4f}s/条"
            )
        return total_updated

    def count_by_filter(self, filter: Dict[str, Any]) -> int:
        """根据过滤条件统计文档数量"""
        try:
            count = self.collection.count_documents(filter)
            print(f"符合条件的文档数量: {count}")
            return count
        except Exception as e:
            print(f"统计文档数量失败: {e}")
            return 0
