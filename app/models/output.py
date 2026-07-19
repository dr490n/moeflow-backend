import os
from app.utils import default
from mongoengine.base.fields import ObjectIdField
from mongoengine.fields import ListField
from app.constants.output import OutputStatus, OutputTypes
from app.exceptions.output import OutputNotExistError
import datetime
from flask import current_app

from app.utils.logging import logger
from mongoengine import DateTimeField, Document, IntField, ReferenceField, StringField
from typing import List, TYPE_CHECKING
import oss2
from app import oss

if TYPE_CHECKING:
    from app.models.project import Project
    from app.models.target import Target
    from app.models.user import User


class Output(Document):
    """项目的导出"""

    # 此日期之后创建的导出使用带日期文件夹的 OSS 路径
    OSS_DATE_FOLDER_CUTOFF = datetime.datetime(2026, 7, 18)

    project = ReferenceField("Project", db_field="p", required=True)
    target = ReferenceField("Target", db_field="t", required=True)
    user = ReferenceField("User", db_field="u")  # 操作人
    status = IntField(db_field="s", default=OutputStatus.QUEUING)
    type = IntField(db_field="ty", default=OutputTypes.ALL)
    file_name = StringField(df_field="fn", default="")
    create_time = DateTimeField(db_field="ct", default=datetime.datetime.utcnow)
    file_ids_include = ListField(ObjectIdField(), default=list)
    file_ids_exclude = ListField(ObjectIdField(), default=list)

    @property
    def oss_dir(self):
        """OSS 存储目录：prefix/[YYYYMMDD/]output_id/"""
        prefix = current_app.config["OSS_OUTPUT_PREFIX"]
        if self.create_time >= self.OSS_DATE_FOLDER_CUTOFF:
            return (
                prefix
                + self.create_time.strftime("%Y%m%d")
                + "/"
                + str(self.id)
                + "/"
            )
        return prefix + str(self.id) + "/"

    @classmethod
    def create(
        cls,
        /,
        *,
        project: "Project",
        target: "Target",
        user: "User",
        type: int,
        file_ids_include: List[str] = None,
        file_ids_exclude: List[str] = None,
    ) -> "Output":
        output = cls(
            project=project,
            target=target,
            user=user,
            type=type,
            file_ids_include=file_ids_include,
            file_ids_exclude=file_ids_exclude,
        ).save()
        return output

    @classmethod
    def delete_real_files(cls, outputs):
        try:
            oss.delete(
                current_app.config["OSS_OUTPUT_PREFIX"],
                [output.create_time.strftime("%Y%m%d") + "/" + str(output.id) + "/" + output.file_name
                 if output.create_time >= cls.OSS_DATE_FOLDER_CUTOFF
                 else str(output.id) + "/" + output.file_name
                 for output in outputs],
            )
            oss.rmdir(
                [
                    os.path.join(
                        current_app.config["OSS_OUTPUT_PREFIX"],
                        *([output.create_time.strftime("%Y%m%d")] if output.create_time >= cls.OSS_DATE_FOLDER_CUTOFF else []),
                        str(output.id),
                    )
                    for output in outputs
                ],
            )
        except oss2.exceptions.NoSuchKey as e:
            logger.error(e)
        except Exception as e:
            logger.error(e)

    def delete_real_file(self):
        try:
            oss.delete(self.oss_dir, self.file_name)
        except oss2.exceptions.NoSuchKey as e:
            logger.error(e)
        except Exception as e:
            logger.error(e)

    def clear(self):
        self.delete()

    @classmethod
    def by_id(cls, id):
        file = cls.objects(id=id).first()
        if file is None:
            raise OutputNotExistError
        return file

    def to_api(self):
        data = {
            "id": str(self.id),
            "project": self.project.to_api(),
            "target": self.target.to_api(),
            "user": default(self.user, None, "to_api"),
            "type": self.type,
            "status": self.status,
            "status_details": OutputStatus.to_api(),
            "file_ids_include": [str(id) for id in self.file_ids_include],
            "file_ids_exclude": [str(id) for id in self.file_ids_exclude],
            "create_time": self.create_time.isoformat(),
        }
        if self.status == OutputStatus.SUCCEEDED:
            data["link"] = oss.sign_url(
                self.oss_dir,
                self.file_name,
                download=True,
            )
        return data
