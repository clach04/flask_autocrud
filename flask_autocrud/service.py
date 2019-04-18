from flask import request
from flask import current_app as cap
from flask.views import MethodView

from sqlalchemy import inspect
from sqlalchemy.exc import ArgumentError
from sqlalchemy.orm import contains_eager
from sqlalchemy.orm import eagerload
from sqlalchemy.orm import joinedload, subqueryload

from sqlalchemy_filters import apply_filters
from sqlalchemy_filters import apply_loads
from sqlalchemy_filters import apply_sort

from sqlalchemy_filters.exceptions import BadSpec
from sqlalchemy_filters.exceptions import FieldNotFound
from sqlalchemy_filters.exceptions import BadFilterFormat
from sqlalchemy_filters.exceptions import BadSortFormat

from .wrapper import get_json
from .wrapper import resp_csv
from .wrapper import resp_json
from .wrapper import no_content
from .wrapper import response_with_links
from .wrapper import response_with_location

from .validators import validate_entity
from .validators import parsing_query_string

from .utils import from_model_to_dict

from .config import ARGUMENT
from .config import HTTP_STATUS


class Service(MethodView):
    __db__ = None
    __model__ = None
    __collection_name__ = 'resources'

    def delete(self, resource_id):
        """

        :param resource_id:
        :return:
        """
        model = self.__model__
        session = self.__db__.session()

        resource = model.query.get(resource_id)
        if not resource:
            return resp_json({'message': 'Not Found'}, code=HTTP_STATUS.NOT_FOUND)

        session.delete(resource)
        session.commit()
        return no_content()

    def get(self, resource_id=None):
        """

        :param resource_id:
        :return:
        """
        response = []
        model = self.__model__
        page = request.args.get(ARGUMENT.STATIC.page)
        limit = request.args.get(ARGUMENT.STATIC.limit)
        export = True if ARGUMENT.STATIC.export in request.args else False
        extended = True if ARGUMENT.STATIC.extended in request.args else False

        if resource_id is not None:
            resource = model.query.get(resource_id)
            if not resource:
                return resp_json({'message': 'Not Found'}, code=HTTP_STATUS.NOT_FOUND)
            return response_with_links(resource)

        if request.path.endswith('meta'):
            return resp_json(model.description())

        fields, statement = parsing_query_string(model)

        if page is not None:
            resources = statement.paginate(
                page=int(page) if page else None,
                per_page=int(limit) if limit else None
            ).items
        else:
            resources = statement.limit(limit).all()

        for r in resources:
            item = r.to_dict(True if extended else False)
            item_keys = item.keys()
            if fields:
                for k in set(item_keys) - set(fields):
                    item.pop(k)
            response.append(item)

        response_builder = resp_csv if export else resp_json
        return response_builder(response, self.__collection_name__)

    def patch(self, resource_id):
        """

        :param resource_id:
        :return:
        """
        model = self.__model__
        session = self.__db__.session()

        data = get_json()
        validate_entity(model, data)

        resource = model.query.get(resource_id)
        if not resource:
            return resp_json({'message': 'Not Found'}, code=HTTP_STATUS.NOT_FOUND)

        resource.update(data)
        session.merge(resource)
        session.commit()

        return response_with_links(resource)

    def post(self):
        """

        :return:
        """
        model = self.__model__
        session = self.__db__.session()

        data = get_json()
        validate_entity(model, data)

        resource = model.query.filter_by(**data).first()
        if not resource:
            resource = model(**data)
            session.add(resource)
            session.commit()
            code = HTTP_STATUS.CREATED
        else:
            code = HTTP_STATUS.CONFLICT

        return response_with_location(resource, code)

    def put(self, resource_id):
        """

        :param resource_id:
        :return:
        """
        model = self.__model__
        session = self.__db__.session()

        data = get_json()
        validate_entity(model, data)

        resource = model.query.get(resource_id)
        if resource:
            resource.update(data)
            session.merge(resource)
            session.commit()

            return response_with_links(resource)

        resource = model(**data)
        session.add(resource)
        session.commit()

        return response_with_links(resource, HTTP_STATUS.CREATED)

    def fetch(self):
        """

        :return:
        """
        response = []
        invalid = []
        model = self.__model__
        query = self.__db__.session.query(self.__model__)

        data = get_json()
        joins = data.get('joins') or {}
        filters = data.get('filters') or []
        fields = data.get('fields') or []
        sort = data.get('sortBy') or []

        cap.logger.debug(query)

        for k in fields:
            if k not in (model.required() + model.optional()):
                invalid.append(k)

        if len(invalid) == 0 and len(fields) > 0:
            query = apply_loads(query, fields)
            cap.logger.debug(query)

        for k in joins.keys():
            loader = None
            instance = None

            for r in inspect(model).relationships:
                if r.key == k.lower():
                    loader = contains_eager
                    instance = getattr(model, r.key)
                if r.key.split('_collection')[0] == k.lower():
                    loader = contains_eager
                    instance = getattr(model, r.key)

            if instance is not None:
                try:
                    query = query.options(loader(instance).load_only(*joins.get(k)))
                    cap.logger.debug(query)
                except ArgumentError:
                    invalid += joins.get(k)
            else:
                invalid.append(k)

        for f in filters:
            try:
                query = apply_filters(query, f)
                cap.logger.debug(query)
            except BadSpec:
                invalid.append(f.get('model'))
            except FieldNotFound:
                invalid.append(f.get('field'))
            except BadFilterFormat:
                invalid.append(f.get('op'))

        for s in sort:
            try:
                query = apply_sort(query, s)
                cap.logger.debug(query)
            except BadSpec:
                invalid.append(s.get('model'))
            except FieldNotFound:
                invalid.append(s.get('field'))
            except BadSortFormat:
                invalid.append(s.get('direction'))

        if len(invalid) > 0:
            return resp_json(invalid, 'invalid', code=HTTP_STATUS.BAD_REQUEST)

        for r in query.all():
            data = from_model_to_dict(r.__dict__)
            response.append(data)

        return resp_json(response, self.__collection_name__)
